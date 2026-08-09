using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security;
using System.Security.Cryptography;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.PlottingServices;
using CadPlotMcp.Core;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;
using AcException = Autodesk.AutoCAD.Runtime.Exception;

namespace CadPlotMcp.AutoCAD
{
    /// <summary>
    /// Owns the pipe bridge and drains approved publish work only from AutoCAD's
    /// main application context. Pipe threads never call the AutoCAD API.
    /// </summary>
    public sealed class AutoCadPublishRuntime : IDisposable
    {
        private readonly NamedPipeCommandHost _host;
        private readonly PublishJobQueue _queue;
        private readonly PublishJobWorker _worker;
        private readonly bool _publishEnabled;
        private bool _disposed;

        public AutoCadPublishRuntime(string adapter, Func<string> productName)
        {
            var pipeName = PipeProtocol.ResolvePipeName(
                Environment.GetEnvironmentVariable("CADPLOT_PIPE_NAME")
            );
            var workspace = Environment.GetEnvironmentVariable("CADPLOT_WORKSPACE_ROOT");
            var acadVersion = Convert.ToString(AcApplication.GetSystemVariable("ACADVER"));
            var runtimeSeries = AutoCadRuntimeIdentity.NormalizeSeries(acadVersion);
            var runtimeSupported = AutoCadRuntimeIdentity.IsSupported(adapter, runtimeSeries);
            var capturedProduct = (productName == null ? "AutoCAD" : productName())
                + " (ACADVER " + (runtimeSeries ?? "unknown")
                + "; raw " + acadVersion + ")";
            var publishRequested = String.Equals(
                Environment.GetEnvironmentVariable("CADPLOT_ENABLE_PUBLISH"),
                "1",
                StringComparison.Ordinal
            ) && !String.IsNullOrWhiteSpace(workspace);

            PublishJobQueue queue = null;
            var publishEnabled = false;
            string publishInitializationError = null;
            if (publishRequested && runtimeSupported)
            {
                try
                {
                    queue = new PublishJobQueue(workspace, ReadQueueCapacity());
                    _queue = queue;
                    _worker = new PublishJobWorker(
                        queue,
                        new AutoCadPublishExecutor(workspace),
                        new PublishReceiptWriter(workspace)
                    );
                    publishEnabled = true;
                }
                catch (Exception exception)
                {
                    if (!(exception is ArgumentException)
                        && !(exception is IOException)
                        && !(exception is UnauthorizedAccessException)
                        && !(exception is SecurityException)
                        && !(exception is CryptographicException))
                        throw;
                    publishInitializationError = "publish_queue_initialization_failed";
                }
            }
            _publishEnabled = publishEnabled;

            var pluginAssembly = Assembly.GetExecutingAssembly();
            var buildCommit = ReadAssemblyMetadata(pluginAssembly, "RepositoryCommit");
            var pluginSha256 = HashAssembly(pluginAssembly);

            _host = new NamedPipeCommandHost(
                pipeName,
                new CommandDispatcher(
                    adapter,
                    () => capturedProduct,
                    workspace,
                    queue,
                    _publishEnabled,
                    buildCommit,
                    pluginSha256,
                    runtimeSeries,
                    runtimeSupported,
                    publishInitializationError,
                    publishRequested && runtimeSupported
                        ? PublishQueueJournal.AuthenticationScheme
                        : null
                )
            );
            _host.Start();
            if (_publishEnabled) AcApplication.Idle += OnIdle;
        }

        private void OnIdle(object sender, EventArgs eventArgs)
        {
            if (!_publishEnabled || _worker == null || _queue.PendingCount == 0) return;
            var result = _worker.ProcessNext();
            if (result.Processed && !result.Succeeded)
            {
                var document = AcApplication.DocumentManager.MdiActiveDocument;
                if (document != null)
                    document.Editor.WriteMessage("\nCadPlot publish failed: " + result.Error);
            }
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            if (_publishEnabled) AcApplication.Idle -= OnIdle;
            _host.Dispose();
        }

        private static int ReadQueueCapacity()
        {
            int parsed;
            return Int32.TryParse(
                Environment.GetEnvironmentVariable("CADPLOT_QUEUE_CAPACITY"),
                out parsed
            ) && parsed >= 1 && parsed <= 100 ? parsed : 20;
        }

        private static string ReadAssemblyMetadata(Assembly assembly, string key)
        {
            foreach (var attribute in assembly.GetCustomAttributes(typeof(AssemblyMetadataAttribute), false))
            {
                var metadata = attribute as AssemblyMetadataAttribute;
                if (metadata != null && String.Equals(metadata.Key, key, StringComparison.Ordinal))
                    return metadata.Value;
            }
            return null;
        }

        private static string HashAssembly(Assembly assembly)
        {
            try
            {
                using (var stream = File.OpenRead(assembly.Location))
                using (var algorithm = SHA256.Create())
                    return BitConverter.ToString(algorithm.ComputeHash(stream))
                        .Replace("-", "")
                        .ToLowerInvariant();
            }
            catch (Exception exception)
            {
                if (exception is IOException
                    || exception is UnauthorizedAccessException
                    || exception is SecurityException)
                    return null;
                throw;
            }
        }
    }

    /// <summary>
    /// Creates layouts and viewports only in the isolated staged drawing, then
    /// publishes one PDF per manifest output with AutoCAD's PlotEngine.
    /// </summary>
    public sealed class AutoCadPublishExecutor : IPublishJobExecutor
    {
        private const double PaperToleranceMillimetres = 2.0;
        private readonly string _trustedWorkspaceRoot;

        public AutoCadPublishExecutor(string trustedWorkspaceRoot)
        {
            _trustedWorkspaceRoot = trustedWorkspaceRoot;
        }

        public PublishExecutionResult Execute(PublishJobRequest request)
        {
            var requestError = PublishJobValidator.Validate(request, _trustedWorkspaceRoot);
            if (requestError != null) return PublishExecutionResult.Failure(requestError);

            PublishManifest manifest;
            var manifestError = PublishManifestReader.TryReadValidated(request, out manifest);
            if (manifestError != null) return PublishExecutionResult.Failure(manifestError);
            if (AnyOutputExists(manifest.Outputs))
                return PublishExecutionResult.Failure("output_already_exists");
            if (IsDrawingAlreadyOpen(request.StagedDrawing))
                return PublishExecutionResult.Failure("staged_drawing_already_open");
            if (PlotFactory.ProcessPlotState != ProcessPlotState.NotPlotting)
                return PublishExecutionResult.Failure("plot_engine_busy");

            Document document = null;
            PublishOutputTransaction outputTransaction = null;
            var previousDocument = AcApplication.DocumentManager.MdiActiveDocument;
            object backgroundPlot = null;
            var backgroundPlotCaptured = false;
            try
            {
                backgroundPlot = AcApplication.GetSystemVariable("BACKGROUNDPLOT");
                backgroundPlotCaptured = true;
                AcApplication.SetSystemVariable("BACKGROUNDPLOT", 0);
                var finalPaths = new List<string>();
                foreach (var output in manifest.Outputs) finalPaths.Add(output.Pdf);
                try
                {
                    outputTransaction = new PublishOutputTransaction(
                        request.OutputDirectory,
                        finalPaths
                    );
                }
                catch (InvalidOperationException exception)
                {
                    if (String.Equals(
                        exception.Message,
                        "output_already_exists",
                        StringComparison.Ordinal
                    )) return PublishExecutionResult.Failure("output_already_exists");
                    throw;
                }
                catch (ArgumentException)
                {
                    return PublishExecutionResult.Failure("output_transaction_invalid");
                }
                document = AcApplication.DocumentManager.Open(request.StagedDrawing, false);
                AcApplication.DocumentManager.MdiActiveDocument = document;
                using (document.LockDocument())
                {
                    ConfigureLayouts(document.Database, manifest);
                    document.Editor.Regen();
                    for (var index = 0; index < manifest.Outputs.Count; index++)
                        PlotLayout(
                            document,
                            manifest.Outputs[index],
                            outputTransaction.GetTemporaryPath(index)
                        );
                }
                // Layouts are execution scaffolding only. Discarding them keeps the
                // staged DWG byte-identical for the post-publish hash audit.
                document.CloseAndDiscard();
                document = null;
                var outputError = outputTransaction.Commit();
                if (outputError != null) return PublishExecutionResult.Failure(outputError);
                return PublishExecutionResult.Success();
            }
            catch (CadPlotPublishException exception)
            {
                return PublishExecutionResult.Failure(exception.Code);
            }
            catch (AcException exception)
            {
                return PublishExecutionResult.Failure("autocad_error:" + exception.ErrorStatus);
            }
            catch (UnauthorizedAccessException)
            {
                return PublishExecutionResult.Failure("job_access_denied");
            }
            catch (SecurityException)
            {
                return PublishExecutionResult.Failure("job_access_denied");
            }
            catch (IOException)
            {
                return PublishExecutionResult.Failure("job_io_error");
            }
            finally
            {
                if (document != null)
                {
                    try { document.CloseAndDiscard(); }
                    catch { }
                }
                if (outputTransaction != null) outputTransaction.Dispose();
                if (previousDocument != null && IsDocumentOpen(previousDocument))
                {
                    try { AcApplication.DocumentManager.MdiActiveDocument = previousDocument; }
                    catch { }
                }
                if (backgroundPlotCaptured)
                {
                    try { AcApplication.SetSystemVariable("BACKGROUNDPLOT", backgroundPlot); }
                    catch { }
                }
            }
        }

        private static void ConfigureLayouts(
            Database database,
            PublishManifest manifest
        )
        {
            ImportTemplateLayouts(database, manifest.TemplateAssets);
            PreflightLayouts(database, manifest.Outputs);
            foreach (var output in manifest.Outputs)
                ConfigureLayout(database, output);
        }

        private static void ImportTemplateLayouts(
            Database destination,
            IList<PublishTemplateAsset> assets
        )
        {
            if (assets == null) return;
            foreach (var asset in assets)
                ImportTemplateLayout(destination, asset);
        }

        private static void ImportTemplateLayout(
            Database destination,
            PublishTemplateAsset asset
        )
        {
            var info = new FileInfo(asset.StagedTemplate);
            if (!info.Exists
                || info.Length != asset.SizeBytes
                || !String.Equals(
                    FileSha256.Compute(asset.StagedTemplate),
                    asset.Sha256,
                    StringComparison.Ordinal
                )) throw new CadPlotPublishException("template_asset_changed");

            using (var destinationCheck = destination.TransactionManager.StartTransaction())
            {
                var layouts = (DBDictionary)destinationCheck.GetObject(
                    destination.LayoutDictionaryId,
                    OpenMode.ForRead
                );
                if (layouts.Contains(asset.Layout))
                    throw new CadPlotPublishException("external_template_layout_exists");
                destinationCheck.Abort();
            }

            using (var external = new Database(false, true))
            {
                external.ReadDwgFile(
                    asset.StagedTemplate,
                    FileOpenMode.OpenForReadAndAllShare,
                    true,
                    String.Empty
                );
                using (var externalTransaction = external.TransactionManager.StartTransaction())
                {
                    var externalLayouts = (DBDictionary)externalTransaction.GetObject(
                        external.LayoutDictionaryId,
                        OpenMode.ForRead
                    );
                    if (!externalLayouts.Contains(asset.Layout))
                        throw new CadPlotPublishException("template_layout_missing");
                    var externalLayout = (Layout)externalTransaction.GetObject(
                        externalLayouts.GetAt(asset.Layout),
                        OpenMode.ForRead
                    );
                    if (externalLayout.ModelType)
                        throw new CadPlotPublishException("template_layout_is_model");
                    var externalSpace = (BlockTableRecord)externalTransaction.GetObject(
                        externalLayout.BlockTableRecordId,
                        OpenMode.ForRead
                    );
                    if (CountFloatingViewports(externalTransaction, externalSpace) != 1)
                        throw new CadPlotPublishException("template_viewport_count");

                    var objectIds = new ObjectIdCollection();
                    foreach (ObjectId objectId in externalSpace) objectIds.Add(objectId);

                    using (var transaction = destination.TransactionManager.StartTransaction())
                    {
                        var blockTable = (BlockTable)transaction.GetObject(
                            destination.BlockTableId,
                            OpenMode.ForWrite
                        );
                        using (var paperSpace = new BlockTableRecord())
                        {
                            paperSpace.Name = NewPaperSpaceBlockName(blockTable);
                            blockTable.Add(paperSpace);
                            transaction.AddNewlyCreatedDBObject(paperSpace, true);
                            external.WblockCloneObjects(
                                objectIds,
                                paperSpace.ObjectId,
                                new IdMapping(),
                                DuplicateRecordCloning.MangleName,
                                false
                            );

                            using (var importedLayout = new Layout())
                            {
                                importedLayout.LayoutName = asset.Layout;
                                importedLayout.AddToLayoutDictionary(
                                    destination,
                                    paperSpace.ObjectId
                                );
                                transaction.AddNewlyCreatedDBObject(importedLayout, true);
                                importedLayout.CopyFrom(externalLayout);
                            }
                        }

                        var externalPageSetups = (DBDictionary)externalTransaction.GetObject(
                            external.PlotSettingsDictionaryId,
                            OpenMode.ForRead
                        );
                        if (!externalPageSetups.Contains(asset.PageSetup))
                            throw new CadPlotPublishException("page_setup_missing");
                        var pageSetups = (DBDictionary)transaction.GetObject(
                            destination.PlotSettingsDictionaryId,
                            OpenMode.ForRead
                        );
                        if (!pageSetups.Contains(asset.PageSetup))
                        {
                            var externalPageSetup = (PlotSettings)externalTransaction.GetObject(
                                externalPageSetups.GetAt(asset.PageSetup),
                                OpenMode.ForRead
                            );
                            using (var pageSetup = new PlotSettings(false))
                            {
                                pageSetup.PlotSettingsName = asset.PageSetup;
                                pageSetup.AddToPlotSettingsDictionary(destination);
                                transaction.AddNewlyCreatedDBObject(pageSetup, true);
                                pageSetup.CopyFrom(externalPageSetup);
                            }
                        }
                        transaction.Commit();
                    }
                    externalTransaction.Abort();
                }
            }
        }

        private static string NewPaperSpaceBlockName(BlockTable blockTable)
        {
            for (var index = 1; index <= 10_000; index++)
            {
                var candidate = "*Paper_Space" + index.ToString();
                if (!blockTable.Has(candidate)) return candidate;
            }
            throw new CadPlotPublishException("template_layout_capacity");
        }

        private static void PreflightLayouts(
            Database database,
            IList<PublishManifestOutput> outputs
        )
        {
            using (var transaction = database.TransactionManager.StartTransaction())
            {
                var layouts = (DBDictionary)transaction.GetObject(
                    database.LayoutDictionaryId,
                    OpenMode.ForRead
                );
                var pageSetups = (DBDictionary)transaction.GetObject(
                    database.PlotSettingsDictionaryId,
                    OpenMode.ForRead
                );
                foreach (var output in outputs)
                {
                    if (layouts.Contains(output.TargetLayout))
                        throw new CadPlotPublishException("target_layout_exists");
                    if (!pageSetups.Contains(output.PageSetup))
                        throw new CadPlotPublishException("page_setup_missing");
                    if (output.TemplateLayout != null)
                    {
                        if (!layouts.Contains(output.TemplateLayout))
                            throw new CadPlotPublishException("template_layout_missing");
                        var template = (Layout)transaction.GetObject(
                            layouts.GetAt(output.TemplateLayout),
                            OpenMode.ForRead
                        );
                        if (template.ModelType)
                            throw new CadPlotPublishException("template_layout_is_model");
                        var templateSpace = (BlockTableRecord)transaction.GetObject(
                            template.BlockTableRecordId,
                            OpenMode.ForRead
                        );
                        if (CountFloatingViewports(transaction, templateSpace) != 1)
                            throw new CadPlotPublishException("template_viewport_count");
                    }
                    var pageSetup = (PlotSettings)transaction.GetObject(
                        pageSetups.GetAt(output.PageSetup),
                        OpenMode.ForRead
                    );
                    if (pageSetup.ModelType)
                        throw new CadPlotPublishException("page_setup_is_model_type");
                    ValidatePageSetup(pageSetup, output);
                }
                transaction.Abort();
            }
        }

        private static void ConfigureLayout(Database database, PublishManifestOutput output)
        {
            var layoutManager = LayoutManager.Current;
            ObjectId layoutId;
            if (output.TemplateLayout == null)
            {
                layoutId = layoutManager.CreateLayout(output.TargetLayout);
            }
            else
            {
                layoutManager.CloneLayout(
                    output.TemplateLayout,
                    output.TargetLayout,
                    LayoutCount(database)
                );
                layoutId = layoutManager.GetLayoutId(output.TargetLayout);
            }
            using (var transaction = database.TransactionManager.StartTransaction())
            {
                var layout = (Layout)transaction.GetObject(layoutId, OpenMode.ForWrite);
                var pageSetups = (DBDictionary)transaction.GetObject(
                    database.PlotSettingsDictionaryId,
                    OpenMode.ForRead
                );
                var pageSetup = (PlotSettings)transaction.GetObject(
                    pageSetups.GetAt(output.PageSetup),
                    OpenMode.ForRead
                );
                layout.CopyFrom(pageSetup);
                ValidatePageSetup(layout, output);

                var paper = EffectivePaper(layout);
                var paperUnitMillimetres = PaperUnitMillimetres(layout.PlotPaperUnits);
                var paperSpace = (BlockTableRecord)transaction.GetObject(
                    layout.BlockTableRecordId,
                    OpenMode.ForWrite
                );
                if (output.TemplateLayout == null)
                {
                    var viewportGeometry = ViewportGeometryCalculator.Calculate(
                        output.PlotGeometry,
                        paper.Item1,
                        paper.Item2,
                        paperUnitMillimetres
                    );
                    RemoveFloatingViewports(transaction, paperSpace);
                    using (var viewport = new Viewport())
                    {
                        viewport.SetDatabaseDefaults(database);
                        viewport.CenterPoint = new Point3d(
                            viewportGeometry.PaperWidthUnits / 2.0,
                            viewportGeometry.PaperHeightUnits / 2.0,
                            0.0
                        );
                        viewport.Width = viewportGeometry.PaperWidthUnits;
                        viewport.Height = viewportGeometry.PaperHeightUnits;
                        ConfigureViewport(viewport, viewportGeometry);
                        paperSpace.AppendEntity(viewport);
                        transaction.AddNewlyCreatedDBObject(viewport, true);
                    }
                }
                else
                {
                    var viewport = SingleFloatingViewport(transaction, paperSpace);
                    viewport.UpgradeOpen();
                    var viewportGeometry = ViewportGeometryCalculator.Calculate(
                        output.PlotGeometry,
                        viewport.Width * paperUnitMillimetres,
                        viewport.Height * paperUnitMillimetres,
                        paperUnitMillimetres
                    );
                    ConfigureViewport(viewport, viewportGeometry);
                }
                transaction.Commit();
            }
        }

        private static void ConfigureViewport(
            Viewport viewport,
            ViewportGeometryResult viewportGeometry
        )
        {
            viewport.ViewDirection = Vector3d.ZAxis;
            viewport.ViewTarget = new Point3d(
                viewportGeometry.ModelCenterX,
                viewportGeometry.ModelCenterY,
                0.0
            );
            // ViewTarget is WCS while ViewCenter is DCS. Target the model
            // window centre and keep the DCS offset at the origin.
            viewport.ViewCenter = Point2d.Origin;
            viewport.TwistAngle = viewportGeometry.QuarterTurn ? Math.PI / 2.0 : 0.0;
            viewport.CustomScale = viewportGeometry.CustomScale;
            viewport.On = true;
            viewport.Locked = true;
        }

        private static int LayoutCount(Database database)
        {
            using (var transaction = database.TransactionManager.StartTransaction())
            {
                var layouts = (DBDictionary)transaction.GetObject(
                    database.LayoutDictionaryId,
                    OpenMode.ForRead
                );
                var count = layouts.Count;
                transaction.Abort();
                return count;
            }
        }

        private static int CountFloatingViewports(
            Transaction transaction,
            BlockTableRecord paperSpace
        )
        {
            var count = 0;
            foreach (ObjectId objectId in paperSpace)
            {
                var viewport = transaction.GetObject(objectId, OpenMode.ForRead) as Viewport;
                if (viewport != null && viewport.Number > 1) count++;
            }
            return count;
        }

        private static Viewport SingleFloatingViewport(
            Transaction transaction,
            BlockTableRecord paperSpace
        )
        {
            Viewport found = null;
            foreach (ObjectId objectId in paperSpace)
            {
                var viewport = transaction.GetObject(objectId, OpenMode.ForRead) as Viewport;
                if (viewport == null || viewport.Number <= 1) continue;
                if (found != null)
                    throw new CadPlotPublishException("template_viewport_count");
                found = viewport;
            }
            if (found == null)
                throw new CadPlotPublishException("template_viewport_count");
            return found;
        }

        private static void PlotLayout(
            Document document,
            PublishManifestOutput output,
            string temporaryPdf
        )
        {
            if (File.Exists(temporaryPdf) || Directory.Exists(temporaryPdf))
                throw new CadPlotPublishException("temporary_output_exists");
            if (PlotFactory.ProcessPlotState != ProcessPlotState.NotPlotting)
                throw new CadPlotPublishException("plot_engine_busy");

            var layoutManager = LayoutManager.Current;
            layoutManager.CurrentLayout = output.TargetLayout;
            AcApplication.SetSystemVariable("TILEMODE", 0);
            document.Editor.SwitchToPaperSpace();

            using (var transaction = document.Database.TransactionManager.StartTransaction())
            using (var plotInfo = new PlotInfo())
            {
                plotInfo.Layout = layoutManager.GetLayoutId(output.TargetLayout);
                using (var validator = new PlotInfoValidator())
                {
                    validator.MediaMatchingPolicy = MatchingPolicy.MatchEnabled;
                    validator.Validate(plotInfo);
                }
                using (var progress = new PlotProgressDialog(false, 1, true))
                using (var engine = PlotFactory.CreatePublishEngine())
                {
                    progress.IsVisible = false;
                    progress.OnBeginPlot();
                    engine.BeginPlot(progress, null);
                    engine.BeginDocument(
                        plotInfo,
                        document.Name,
                        null,
                        1,
                        true,
                        temporaryPdf
                    );
                    using (var pageInfo = new PlotPageInfo())
                    {
                        progress.OnBeginSheet();
                        engine.BeginPage(pageInfo, plotInfo, true, null);
                        engine.BeginGenerateGraphics(null);
                        engine.EndGenerateGraphics(null);
                        engine.EndPage(null);
                        progress.OnEndSheet();
                    }
                    engine.EndDocument(null);
                    progress.OnEndPlot();
                    engine.EndPlot(null);
                }
                transaction.Abort();
            }
        }

        private static void ValidatePageSetup(
            PlotSettings settings,
            PublishManifestOutput output
        )
        {
            if (!SameText(settings.PlotConfigurationName, output.Plotter))
                throw new CadPlotPublishException("plotter_mismatch");
            if (!SameText(settings.CurrentStyleSheet, output.PlotStyle))
                throw new CadPlotPublishException("plot_style_mismatch");
            if (output.CanonicalMedia != null
                && !String.Equals(
                    settings.CanonicalMediaName,
                    output.CanonicalMedia,
                    StringComparison.Ordinal
                ))
                throw new CadPlotPublishException("canonical_media_mismatch");
            if (settings.PlotType != Autodesk.AutoCAD.DatabaseServices.PlotType.Layout)
                throw new CadPlotPublishException("page_setup_not_layout_plot");
            if (settings.UseStandardScale)
            {
                if (settings.StdScaleType != StdScaleType.StdScale1To1)
                    throw new CadPlotPublishException("page_setup_scale_not_one_to_one");
            }
            else
            {
                var customScale = settings.CustomPrintScale;
                if (!PublishGeometryContract.IsOneToOneCustomScale(
                    customScale.Numerator,
                    customScale.Denominator
                ))
                    throw new CadPlotPublishException("page_setup_scale_not_one_to_one");
            }

            var paper = EffectivePaper(settings);
            var expectedWidth = output.PlotGeometry.PaperWidthMillimetres;
            var expectedHeight = output.PlotGeometry.PaperHeightMillimetres;
            if (!DimensionsMatch(
                paper.Item1,
                paper.Item2,
                expectedWidth,
                expectedHeight
            ))
                throw new CadPlotPublishException("paper_size_mismatch");
        }

        private static Tuple<double, double> EffectivePaper(PlotSettings settings)
        {
            var unitMillimetres = PaperUnitMillimetres(settings.PlotPaperUnits);
            var width = settings.PlotPaperSize.X * unitMillimetres;
            var height = settings.PlotPaperSize.Y * unitMillimetres;
            if (settings.PlotRotation == PlotRotation.Degrees090
                || settings.PlotRotation == PlotRotation.Degrees270)
                return Tuple.Create(height, width);
            return Tuple.Create(width, height);
        }

        private static double PaperUnitMillimetres(PlotPaperUnit units)
        {
            if (units == PlotPaperUnit.Millimeters) return 1.0;
            if (units == PlotPaperUnit.Inches) return 25.4;
            throw new CadPlotPublishException("unsupported_paper_units");
        }

        private static void RemoveFloatingViewports(
            Transaction transaction,
            BlockTableRecord paperSpace
        )
        {
            foreach (ObjectId objectId in paperSpace)
            {
                var viewport = transaction.GetObject(objectId, OpenMode.ForRead) as Viewport;
                if (viewport != null && viewport.Number > 1)
                {
                    viewport.UpgradeOpen();
                    viewport.Erase();
                }
            }
        }

        private static bool AnyOutputExists(IList<PublishManifestOutput> outputs)
        {
            foreach (var output in outputs)
                if (File.Exists(output.Pdf)) return true;
            return false;
        }

        private static bool IsDrawingAlreadyOpen(string path)
        {
            var expected = Path.GetFullPath(path);
            foreach (Document document in AcApplication.DocumentManager)
            {
                if (String.Equals(
                    Path.GetFullPath(document.Name),
                    expected,
                    StringComparison.OrdinalIgnoreCase
                )) return true;
            }
            return false;
        }

        private static bool IsDocumentOpen(Document expected)
        {
            foreach (Document document in AcApplication.DocumentManager)
                if (Object.ReferenceEquals(document, expected)) return true;
            return false;
        }

        private static bool DimensionsMatch(
            double actualWidth,
            double actualHeight,
            double expectedWidth,
            double expectedHeight
        )
        {
            return (Close(actualWidth, expectedWidth) && Close(actualHeight, expectedHeight))
                || (Close(actualWidth, expectedHeight) && Close(actualHeight, expectedWidth));
        }

        private static bool Close(double left, double right)
        {
            return Math.Abs(left - right) <= PaperToleranceMillimetres;
        }

        private static bool SameText(string left, string right)
        {
            return String.Equals(left, right, StringComparison.OrdinalIgnoreCase);
        }
    }

    internal sealed class CadPlotPublishException : Exception
    {
        public string Code { get; private set; }

        public CadPlotPublishException(string code) : base(code)
        {
            Code = code;
        }
    }
}
