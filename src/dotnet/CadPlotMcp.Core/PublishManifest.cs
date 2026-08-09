using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text.RegularExpressions;

namespace CadPlotMcp.Core
{
    [DataContract]
    public sealed class PublishManifest
    {
        [DataMember(Name = "schema_version")] public int SchemaVersion { get; set; }
        [DataMember(Name = "job_id")] public string JobId { get; set; }
        [DataMember(Name = "state")] public string State { get; set; }
        [DataMember(Name = "plan_id")] public string PlanId { get; set; }
        [DataMember(Name = "staged_drawing")] public string StagedDrawing { get; set; }
        [DataMember(Name = "output_directory")] public string OutputDirectory { get; set; }
        [DataMember(Name = "source_fingerprint")] public PublishSourceFingerprint SourceFingerprint { get; set; }
        [DataMember(Name = "outputs")] public List<PublishManifestOutput> Outputs { get; set; }
    }

    [DataContract]
    public sealed class PublishSourceFingerprint
    {
        [DataMember(Name = "sha256")] public string Sha256 { get; set; }
        [DataMember(Name = "size_bytes")] public long SizeBytes { get; set; }
    }

    [DataContract]
    public sealed class PublishManifestOutput
    {
        [DataMember(Name = "sheet_index")] public int SheetIndex { get; set; }
        [DataMember(Name = "frame_handle")] public string FrameHandle { get; set; }
        [DataMember(Name = "pdf")] public string Pdf { get; set; }
        [DataMember(Name = "target_layout")] public string TargetLayout { get; set; }
        [DataMember(Name = "page_setup")] public string PageSetup { get; set; }
        [DataMember(Name = "plotter")] public string Plotter { get; set; }
        [DataMember(Name = "plot_style")] public string PlotStyle { get; set; }
        [DataMember(Name = "canonical_media", EmitDefaultValue = false)] public string CanonicalMedia { get; set; }
        [DataMember(Name = "template_layout", EmitDefaultValue = false)] public string TemplateLayout { get; set; }
        [DataMember(Name = "plot_geometry")] public PublishPlotGeometry PlotGeometry { get; set; }
        [DataMember(Name = "status")] public string Status { get; set; }
    }

    [DataContract]
    public sealed class PublishPlotGeometry
    {
        [DataMember(Name = "window")] public PublishPlotWindow Window { get; set; }
        [DataMember(Name = "rotation_degrees")] public int RotationDegrees { get; set; }
        [DataMember(Name = "scale_denominator")] public double ScaleDenominator { get; set; }
        [DataMember(Name = "derived_scale_denominator")] public double DerivedScaleDenominator { get; set; }
        [DataMember(Name = "scale_tolerance_ratio")] public double ScaleToleranceRatio { get; set; }
        [DataMember(Name = "drawing_unit_mm")] public double DrawingUnitMillimetres { get; set; }
        [DataMember(Name = "paper_width_mm")] public double PaperWidthMillimetres { get; set; }
        [DataMember(Name = "paper_height_mm")] public double PaperHeightMillimetres { get; set; }
    }

    [DataContract]
    public sealed class PublishPlotWindow
    {
        [DataMember(Name = "min_x")] public double MinX { get; set; }
        [DataMember(Name = "min_y")] public double MinY { get; set; }
        [DataMember(Name = "max_x")] public double MaxX { get; set; }
        [DataMember(Name = "max_y")] public double MaxY { get; set; }
    }

    public static class PublishManifestReader
    {
        public const long MaxManifestBytes = 1024 * 1024;
        private static readonly Regex SafeLayoutName = new Regex(
            "^[A-Za-z0-9_-]{1,255}$",
            RegexOptions.CultureInvariant
        );
        private static readonly Regex Sha256Pattern = new Regex(
            "^[0-9a-f]{64}$",
            RegexOptions.CultureInvariant
        );

        public static string Validate(PublishJobRequest request)
        {
            PublishManifest ignored;
            return TryReadValidated(request, out ignored);
        }

        public static string TryReadValidated(
            PublishJobRequest request,
            out PublishManifest manifest
        )
        {
            manifest = null;
            var info = new FileInfo(request.ManifestPath);
            if (!info.Exists) return "job_files_missing";
            if (info.Length > MaxManifestBytes) return "manifest_too_large";

            try
            {
                using (var stream = info.OpenRead())
                {
                    manifest = (PublishManifest)new DataContractJsonSerializer(
                        typeof(PublishManifest)
                    ).ReadObject(stream);
                }
            }
            catch (Exception exception)
            {
                if (exception is SerializationException || exception is InvalidCastException)
                {
                    manifest = null;
                    return "invalid_manifest_json";
                }
                throw;
            }
            if (manifest == null || manifest.SchemaVersion != 1) return "unsupported_manifest_schema";
            if (!String.Equals(manifest.State, "staged", StringComparison.Ordinal))
                return "invalid_manifest_state";
            if (!String.Equals(manifest.PlanId, request.PlanId, StringComparison.Ordinal))
                return "manifest_plan_mismatch";

            var jobRoot = Path.GetDirectoryName(Path.GetFullPath(request.ManifestPath));
            if (String.IsNullOrWhiteSpace(jobRoot)) return "invalid_job_root";
            if (!String.Equals(manifest.JobId, Path.GetFileName(jobRoot), StringComparison.Ordinal))
                return "manifest_job_mismatch";
            if (!SamePath(manifest.StagedDrawing, request.StagedDrawing))
                return "manifest_drawing_mismatch";
            if (!SamePath(manifest.OutputDirectory, request.OutputDirectory))
                return "manifest_output_mismatch";
            if (manifest.SourceFingerprint == null
                || !Sha256Pattern.IsMatch(manifest.SourceFingerprint.Sha256 ?? String.Empty)
                || manifest.SourceFingerprint.SizeBytes < 1)
                return "invalid_source_fingerprint";
            var stagedInfo = new FileInfo(request.StagedDrawing);
            if (!stagedInfo.Exists || stagedInfo.Length != manifest.SourceFingerprint.SizeBytes)
                return "staged_drawing_changed";
            if (!String.Equals(
                FileSha256.Compute(request.StagedDrawing),
                manifest.SourceFingerprint.Sha256,
                StringComparison.Ordinal
            )) return "staged_drawing_changed";
            if (manifest.Outputs == null || manifest.Outputs.Count != request.SheetCount)
                return "manifest_sheet_count_mismatch";

            var outputs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var layouts = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (var index = 0; index < manifest.Outputs.Count; index++)
            {
                var error = ValidateOutput(
                    manifest.Outputs[index],
                    index + 1,
                    request.OutputDirectory,
                    outputs,
                    layouts
                );
                if (error != null) return error;
            }
            return null;
        }

        private static string ValidateOutput(
            PublishManifestOutput output,
            int expectedSheetIndex,
            string outputDirectory,
            HashSet<string> seen,
            HashSet<string> seenLayouts
        )
        {
            if (output == null || output.SheetIndex != expectedSheetIndex)
                return "invalid_manifest_output";
            if (!String.Equals(output.Status, "pending", StringComparison.Ordinal))
                return "invalid_output_state";
            string pdf;
            try { pdf = Path.GetFullPath(output.Pdf ?? String.Empty); }
            catch (Exception exception)
            {
                if (exception is ArgumentException || exception is NotSupportedException || exception is PathTooLongException)
                    return "invalid_pdf_path";
                throw;
            }
            if (!SamePath(Path.GetDirectoryName(pdf), outputDirectory)
                || !String.Equals(Path.GetExtension(pdf), ".pdf", StringComparison.OrdinalIgnoreCase))
                return "pdf_outside_job";
            if (!seen.Add(pdf)) return "duplicate_pdf_path";
            if (String.IsNullOrWhiteSpace(output.FrameHandle)
                || String.IsNullOrWhiteSpace(output.PageSetup)
                || String.IsNullOrWhiteSpace(output.Plotter)
                || String.IsNullOrWhiteSpace(output.PlotStyle)
                || String.IsNullOrWhiteSpace(output.TargetLayout)
                || !SafeLayoutName.IsMatch(output.TargetLayout))
                return "invalid_sheet_metadata";
            if (output.CanonicalMedia != null && String.IsNullOrWhiteSpace(output.CanonicalMedia))
                return "invalid_canonical_media";
            if (output.TemplateLayout != null
                && (!SafeResourceName(output.TemplateLayout)
                    || String.Equals(output.TemplateLayout, output.TargetLayout, StringComparison.OrdinalIgnoreCase)))
                return "invalid_template_layout";
            if (!seenLayouts.Add(output.TargetLayout)) return "duplicate_layout_target";
            return ValidateGeometry(output.PlotGeometry);
        }

        private static string ValidateGeometry(PublishPlotGeometry geometry)
        {
            return PublishGeometryContract.Validate(geometry);
        }

        private static bool SafeResourceName(string value)
        {
            if (String.IsNullOrWhiteSpace(value) || value.Length > 255) return false;
            foreach (var character in value)
                if (Char.IsControl(character)) return false;
            return true;
        }

        private static bool SamePath(string left, string right)
        {
            if (String.IsNullOrWhiteSpace(left) || String.IsNullOrWhiteSpace(right)) return false;
            return String.Equals(
                Path.GetFullPath(left).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                Path.GetFullPath(right).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                StringComparison.OrdinalIgnoreCase
            );
        }

    }
}
