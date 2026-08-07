using System;
using System.Collections.Generic;
using System.IO;
using System.Security;
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
            var workspace = Environment.GetEnvironmentVariable("CADPLOT_WORKSPACE_ROOT");
            var acadVersion = Convert.ToString(AcApplication.GetSystemVariable("ACADVER"));
            var capturedProduct = (productName == null ? "AutoCAD" : productName())
                + " (ACADVER " + acadVersion + ")";
            var publishRequested = String.Equals(
                Environment.GetEnvironmentVariable("CADPLOT_ENABLE_PUBLISH"),
                "1",
                StringComparison.Ordinal
            ) && !String.IsNullOrWhiteSpace(workspace);

            PublishJobQueue queue = null;
            var publishEnabled = false;
            if (publishRequested)
            {
                try
                {
                    queue = new PublishJobQueue(workspace, ReadQueueCapacity());
                    _queue = queue;
                    _worker = new PublishJobWorker(queue, new AutoCadPublishExecutor(workspace));
                    publishEnabled = true;
                }
                catch (Exception exception)
                {
                    if (!(exception is ArgumentException)
                        && !(exception is IOException)
                        && !(exception is UnauthorizedAccessException)
                        && !(exception is SecurityException))
                        throw;
                }
            }
            _publishEnabled = publishEnabled;

            _host = new NamedPipeCommandHost(
                null,
                new CommandDispatcher(
                    adapter,
                    () => capturedProduct,
                    workspace,
                    queue,
                    _publishEnabled
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
            var previousDocument = AcApplication.DocumentManager.MdiActiveDocument;
            object backgroundPlot = null;
            var backgroundPlotCaptured = false;
            try
            {
                backgroundPlot = AcApplication.GetSystemVariable("BACKGROUNDPLOT");
                backgroundPlotCaptured = true;
                AcApplication.SetSystemVariable("BACKGROUNDPLOT", 0);
                document = AcApplication.DocumentManager.Open(request.StagedDrawing, false);
                AcApplication.DocumentManager.MdiActiveDocument = document;
                using (document.LockDocument())
                {
                    ConfigureLayouts(document.Database, manifest.Outputs);
                    document.Editor.Regen();
                    foreach (var output in manifest.Outputs)
                        PlotLayout(document, output);
                }
                // Layouts are execution scaffolding only. Discarding them keeps the
                // staged DWG byte-identical for the post-publish hash audit.
                document.CloseAndDiscard();
                document = null;
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
            IList<PublishManifestOutput> outputs
        )
        {
            PreflightLayouts(database, outputs);
            foreach (var output in outputs)
                ConfigureLayout(database, output);
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
            var layoutId = layoutManager.CreateLayout(output.TargetLayout);
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
                var viewportGeometry = ViewportGeometryCalculator.Calculate(
                    output.PlotGeometry,
                    paper.Item1,
                    paper.Item2,
                    paperUnitMillimetres
                );
                var paperSpace = (BlockTableRecord)transaction.GetObject(
                    layout.BlockTableRecordId,
                    OpenMode.ForWrite
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
                    paperSpace.AppendEntity(viewport);
                    transaction.AddNewlyCreatedDBObject(viewport, true);
                }
                transaction.Commit();
            }
        }

        private static void PlotLayout(Document document, PublishManifestOutput output)
        {
            if (File.Exists(output.Pdf))
                throw new CadPlotPublishException("output_already_exists");
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
                        output.Pdf
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
