using System;
using System.Globalization;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;

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
        [DataMember(Name = "completed_utc")] public string CompletedUtc { get; set; }
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
                    SchemaVersion = 1,
                    PlanId = request.PlanId,
                    ManifestSha256 = request.ManifestSha256,
                    State = execution.Succeeded ? "succeeded" : "failed",
                    Error = execution.Succeeded ? null : execution.Error,
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
