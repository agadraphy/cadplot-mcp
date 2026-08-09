using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security;
using System.Security.Cryptography;
using System.Text;

namespace CadPlotMcp.Core
{
    public interface IPublishReceiptWriter
    {
        string Write(PublishJobRequest request, PublishExecutionResult execution);
    }

    [DataContract]
    public sealed class PublishReceipt
    {
        [DataMember(Name = "schema_version")] public int SchemaVersion { get; set; }
        [DataMember(Name = "plan_id")] public string PlanId { get; set; }
        [DataMember(Name = "manifest_sha256")] public string ManifestSha256 { get; set; }
        [DataMember(Name = "state")] public string State { get; set; }
        [DataMember(Name = "error", EmitDefaultValue = false)] public string Error { get; set; }
        [DataMember(Name = "output_count")] public int OutputCount { get; set; }
        [DataMember(Name = "outputs_sha256")] public string OutputsSha256 { get; set; }
        [DataMember(Name = "completed_utc")] public string CompletedUtc { get; set; }
    }

    public static class PublishReceiptOutputBinding
    {
        private static readonly byte[] Domain = Encoding.ASCII.GetBytes(
            "cadplot-receipt-outputs-v1\0"
        );

        public static string TryCompute(
            PublishManifest manifest,
            out int outputCount,
            out string outputsSha256
        )
        {
            outputCount = 0;
            outputsSha256 = null;
            if (manifest == null || manifest.Outputs == null
                || manifest.Outputs.Count < 1 || manifest.Outputs.Count > 5000)
                return "receipt_outputs_invalid";

            try
            {
                using (var canonical = new MemoryStream())
                {
                    canonical.Write(Domain, 0, Domain.Length);
                    WriteInt64(canonical, manifest.Outputs.Count);
                    foreach (var output in manifest.Outputs)
                    {
                        var path = Path.GetFullPath(output.Pdf ?? String.Empty);
                        var fileName = Path.GetFileName(path);
                        if (String.IsNullOrWhiteSpace(fileName))
                            return "receipt_output_invalid";
                        foreach (var character in fileName)
                            if (Char.IsControl(character))
                                return "receipt_output_invalid";

                        var info = new FileInfo(path);
                        if (!info.Exists || (info.Attributes & FileAttributes.Directory) != 0)
                            return "receipt_output_missing";
                        if ((info.Attributes & FileAttributes.ReparsePoint) != 0)
                            return "receipt_output_redirected";
                        var size = info.Length;
                        if (size < 1) return "receipt_output_empty";
                        var digest = FileSha256.Compute(path);
                        info.Refresh();
                        if (!info.Exists || info.Length != size)
                            return "receipt_output_changed";

                        var fileNameBytes = Encoding.UTF8.GetBytes(fileName);
                        WriteInt64(canonical, output.SheetIndex);
                        WriteInt64(canonical, fileNameBytes.Length);
                        canonical.Write(fileNameBytes, 0, fileNameBytes.Length);
                        WriteInt64(canonical, size);
                        var digestBytes = HexToBytes(digest);
                        canonical.Write(digestBytes, 0, digestBytes.Length);
                    }
                    canonical.Position = 0;
                    using (var algorithm = SHA256.Create())
                    {
                        outputsSha256 = BitConverter.ToString(
                            algorithm.ComputeHash(canonical)
                        ).Replace("-", String.Empty).ToLowerInvariant();
                    }
                }
                outputCount = manifest.Outputs.Count;
                return null;
            }
            catch (Exception exception)
            {
                if (exception is IOException
                    || exception is UnauthorizedAccessException
                    || exception is SecurityException)
                    return "receipt_output_unavailable";
                throw;
            }
        }

        private static void WriteInt64(Stream stream, long value)
        {
            if (value < 0) throw new InvalidDataException("Negative receipt binding value.");
            var bytes = new byte[8];
            for (var index = 7; index >= 0; index--)
            {
                bytes[index] = (byte)(value & 0xff);
                value >>= 8;
            }
            stream.Write(bytes, 0, bytes.Length);
        }

        private static byte[] HexToBytes(string value)
        {
            if (String.IsNullOrWhiteSpace(value) || value.Length != 64)
                throw new InvalidDataException("Invalid SHA-256 digest.");
            var result = new byte[32];
            for (var index = 0; index < result.Length; index++)
                result[index] = Convert.ToByte(value.Substring(index * 2, 2), 16);
            return result;
        }
    }

    public sealed class PublishReceiptWriter : IPublishReceiptWriter
    {
        public const string ReceiptFileName = "receipt.json";
        private readonly string _trustedWorkspaceRoot;

        public PublishReceiptWriter(string trustedWorkspaceRoot)
        {
            _trustedWorkspaceRoot = trustedWorkspaceRoot;
        }

        public string Write(PublishJobRequest request, PublishExecutionResult execution)
        {
            if (execution == null) return "receipt_execution_missing";
            var validation = PublishJobValidator.Validate(request, _trustedWorkspaceRoot);
            if (validation != null) return validation;

            var outputCount = 0;
            string outputsSha256 = null;
            if (execution.Succeeded)
            {
                PublishManifest manifest;
                validation = PublishManifestReader.TryReadValidated(request, out manifest);
                if (validation != null) return validation;
                validation = PublishReceiptOutputBinding.TryCompute(
                    manifest,
                    out outputCount,
                    out outputsSha256
                );
                if (validation != null) return validation;
                if (outputCount != request.SheetCount) return "receipt_output_count_mismatch";
            }

            var jobRoot = Path.GetDirectoryName(Path.GetFullPath(request.ManifestPath));
            if (String.IsNullOrWhiteSpace(jobRoot)) return "invalid_job_root";
            var receiptPath = Path.Combine(jobRoot, ReceiptFileName);
            if (File.Exists(receiptPath)) return "receipt_exists";

            var temporaryPath = Path.Combine(
                jobRoot,
                ".receipt-" + Guid.NewGuid().ToString("N") + ".tmp"
            );
            try
            {
                var receipt = new PublishReceipt
                {
                    SchemaVersion = 2,
                    PlanId = request.PlanId,
                    ManifestSha256 = request.ManifestSha256,
                    State = execution.Succeeded ? "succeeded" : "failed",
                    Error = execution.Succeeded ? null : execution.Error,
                    OutputCount = outputCount,
                    OutputsSha256 = outputsSha256,
                    CompletedUtc = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                };
                using (var stream = new FileStream(
                    temporaryPath,
                    FileMode.CreateNew,
                    FileAccess.Write,
                    FileShare.None
                ))
                {
                    new DataContractJsonSerializer(typeof(PublishReceipt)).WriteObject(
                        stream,
                        receipt
                    );
                    stream.Flush(true);
                }
                if (File.Exists(receiptPath)) return "receipt_exists";
                File.Move(temporaryPath, receiptPath);
                temporaryPath = null;
                return null;
            }
            catch (Exception exception)
            {
                if (exception is IOException || exception is UnauthorizedAccessException)
                    return "receipt_write_failed";
                throw;
            }
            finally
            {
                if (temporaryPath != null && File.Exists(temporaryPath))
                {
                    try { File.Delete(temporaryPath); }
                    catch { }
                }
            }
        }
    }
}
