using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security;
using System.Text.RegularExpressions;

namespace CadPlotMcp.Core
{
    [DataContract]
    public sealed class PublishQueueRequestRecord
    {
        [DataMember(Name = "schema_version")] public int SchemaVersion { get; set; }
        [DataMember(Name = "plan_id")] public string PlanId { get; set; }
        [DataMember(Name = "manifest_path")] public string ManifestPath { get; set; }
        [DataMember(Name = "manifest_sha256")] public string ManifestSha256 { get; set; }
        [DataMember(Name = "staged_drawing")] public string StagedDrawing { get; set; }
        [DataMember(Name = "output_directory")] public string OutputDirectory { get; set; }
        [DataMember(Name = "sheet_count")] public int SheetCount { get; set; }
        [DataMember(Name = "queued_utc")] public string QueuedUtc { get; set; }
        [DataMember(Name = "authentication_version")] public int AuthenticationVersion { get; set; }
        [DataMember(Name = "authentication_tag")] public string AuthenticationTag { get; set; }
    }

    [DataContract]
    public sealed class PublishQueueStartedRecord
    {
        [DataMember(Name = "schema_version")] public int SchemaVersion { get; set; }
        [DataMember(Name = "plan_id")] public string PlanId { get; set; }
        [DataMember(Name = "manifest_sha256")] public string ManifestSha256 { get; set; }
        [DataMember(Name = "started_utc")] public string StartedUtc { get; set; }
        [DataMember(Name = "authentication_version")] public int AuthenticationVersion { get; set; }
        [DataMember(Name = "authentication_tag")] public string AuthenticationTag { get; set; }
    }

    internal sealed class PublishQueueRecovery
    {
        public List<PublishJobRequest> Pending { get; private set; }
        public Dictionary<string, PublishJobSnapshot> States { get; private set; }
        public int RecoveredOnStartup { get; set; }
        public int InterruptedOnStartup { get; set; }

        public PublishQueueRecovery()
        {
            Pending = new List<PublishJobRequest>();
            States = new Dictionary<string, PublishJobSnapshot>(StringComparer.Ordinal);
        }
    }

    /// <summary>
    /// Persists an exact approved queue intent beside each staged job. A pending intent may be
    /// recovered after restart; a started intent without a terminal receipt is never replayed.
    /// </summary>
    public sealed class PublishQueueJournal
    {
        public const string AuthenticationScheme = "windows-dpapi-current-user+hmac-sha256-v1";
        public const string PendingFileName = ".cadplot-queue-request.json";
        public const string StartedFileName = ".cadplot-queue-started.json";
        private const int MaxRecordBytes = 65536;
        private const int MaxJobDirectories = 50000;
        private static readonly Regex JobIdPattern = new Regex(
            "^job-[0-9]{8}T(?:[0-9]{6}|[0-9]{12})Z-[0-9a-f]{12}$",
            RegexOptions.CultureInvariant
        );
        private static readonly Regex SafeErrorPattern = new Regex(
            "^[A-Za-z0-9_:-]{1,128}$",
            RegexOptions.CultureInvariant
        );
        private readonly string _trustedWorkspaceRoot;
        private readonly PublishQueueAuthenticator _authenticator;

        internal PublishQueueJournal(string trustedWorkspaceRoot, byte[] authenticationKey)
        {
            _trustedWorkspaceRoot = Path.GetFullPath(trustedWorkspaceRoot ?? String.Empty);
            _authenticator = new PublishQueueAuthenticator(authenticationKey);
        }

        internal PublishQueueRecovery Recover(int capacity)
        {
            var recovery = new PublishQueueRecovery();
            var matchingDirectories = 0;
            foreach (var jobRoot in Directory.EnumerateDirectories(_trustedWorkspaceRoot))
            {
                if (!JobIdPattern.IsMatch(Path.GetFileName(jobRoot))) continue;
                matchingDirectories++;
                if (matchingDirectories > MaxJobDirectories)
                    throw new InvalidDataException("publish_queue_job_limit_exceeded");
                if ((File.GetAttributes(jobRoot) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("publish_queue_job_redirected");

                var pendingPath = Path.Combine(jobRoot, PendingFileName);
                var startedPath = Path.Combine(jobRoot, StartedFileName);
                var receiptPath = Path.Combine(jobRoot, PublishReceiptWriter.ReceiptFileName);
                if (!File.Exists(pendingPath))
                {
                    if (File.Exists(startedPath))
                        throw new InvalidDataException("publish_queue_request_missing");
                    continue;
                }

                RequirePlainFile(pendingPath);
                var pendingRecord = ReadRecord<PublishQueueRequestRecord>(pendingPath);
                if (!_authenticator.Verify(pendingRecord))
                    throw new InvalidDataException("publish_queue_request_authentication_failed");
                var request = ValidateRequestRecord(pendingRecord, jobRoot);
                if (recovery.States.ContainsKey(request.PlanId))
                    throw new InvalidDataException("publish_queue_duplicate_plan");

                PublishJobSnapshot snapshot;
                if (File.Exists(receiptPath))
                {
                    if (!File.Exists(startedPath))
                        throw new InvalidDataException("publish_queue_started_marker_missing");
                    RequirePlainFile(startedPath);
                    var startedRecord = ReadRecord<PublishQueueStartedRecord>(startedPath);
                    if (!_authenticator.Verify(startedRecord))
                        throw new InvalidDataException("publish_queue_started_authentication_failed");
                    ValidateStartedRecord(startedRecord, request);
                    RequirePlainFile(receiptPath);
                    snapshot = ReadReceiptSnapshot(receiptPath, request);
                }
                else if (File.Exists(startedPath))
                {
                    RequirePlainFile(startedPath);
                    var startedRecord = ReadRecord<PublishQueueStartedRecord>(startedPath);
                    if (!_authenticator.Verify(startedRecord))
                        throw new InvalidDataException("publish_queue_started_authentication_failed");
                    ValidateStartedRecord(startedRecord, request);
                    snapshot = new PublishJobSnapshot
                    {
                        PlanId = request.PlanId,
                        State = PublishJobState.Failed,
                        Error = "job_interrupted",
                    };
                    recovery.InterruptedOnStartup++;
                }
                else
                {
                    snapshot = new PublishJobSnapshot
                    {
                        PlanId = request.PlanId,
                        State = PublishJobState.Pending,
                    };
                    recovery.Pending.Add(request);
                    recovery.RecoveredOnStartup++;
                }
                recovery.States.Add(request.PlanId, snapshot);
            }
            if (recovery.Pending.Count > capacity)
                throw new InvalidDataException("publish_queue_recovery_exceeds_capacity");
            return recovery;
        }

        public string RecordPending(PublishJobRequest request)
        {
            var jobRoot = Path.GetDirectoryName(Path.GetFullPath(request.ManifestPath));
            var pendingPath = Path.Combine(jobRoot, PendingFileName);
            var startedPath = Path.Combine(jobRoot, StartedFileName);
            var receiptPath = Path.Combine(jobRoot, PublishReceiptWriter.ReceiptFileName);
            if (File.Exists(receiptPath)) return "job_already_completed";
            if (File.Exists(pendingPath) || File.Exists(startedPath)) return "duplicate_plan";
            var record = new PublishQueueRequestRecord
            {
                SchemaVersion = 1,
                PlanId = request.PlanId,
                ManifestPath = request.ManifestPath,
                ManifestSha256 = request.ManifestSha256,
                StagedDrawing = request.StagedDrawing,
                OutputDirectory = request.OutputDirectory,
                SheetCount = request.SheetCount,
                QueuedUtc = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                AuthenticationVersion = 1,
            };
            record.AuthenticationTag = _authenticator.Sign(record);
            try
            {
                WriteAtomicCreate(pendingPath, record);
                return null;
            }
            catch (Exception exception)
            {
                if (!IsBoundedFileException(exception)) throw;
                return File.Exists(pendingPath) ? "duplicate_plan" : "queue_state_write_failed";
            }
        }

        public string RecordStarted(PublishJobRequest request)
        {
            var jobRoot = Path.GetDirectoryName(Path.GetFullPath(request.ManifestPath));
            var pendingPath = Path.Combine(jobRoot, PendingFileName);
            var startedPath = Path.Combine(jobRoot, StartedFileName);
            var receiptPath = Path.Combine(jobRoot, PublishReceiptWriter.ReceiptFileName);
            try
            {
                if (!File.Exists(pendingPath)) return "queue_state_missing";
                RequirePlainFile(pendingPath);
                var pendingRecord = ReadRecord<PublishQueueRequestRecord>(pendingPath);
                if (!_authenticator.Verify(pendingRecord)) return "queue_state_authentication_failed";
                var persisted = ValidateRequestRecord(pendingRecord, jobRoot);
                if (!SameRequest(persisted, request)) return "queue_state_changed";
                if (File.Exists(receiptPath)) return "job_already_completed";
                if (File.Exists(startedPath)) return "job_already_started";
                var record = new PublishQueueStartedRecord
                {
                    SchemaVersion = 1,
                    PlanId = request.PlanId,
                    ManifestSha256 = request.ManifestSha256,
                    StartedUtc = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                    AuthenticationVersion = 1,
                };
                record.AuthenticationTag = _authenticator.Sign(record);
                WriteAtomicCreate(startedPath, record);
                return null;
            }
            catch (InvalidDataException)
            {
                return "queue_state_invalid";
            }
            catch (Exception exception)
            {
                if (!IsBoundedFileException(exception)) throw;
                return File.Exists(startedPath)
                    ? "job_already_started"
                    : "queue_state_write_failed";
            }
        }

        private PublishJobRequest ValidateRequestRecord(
            PublishQueueRequestRecord record,
            string expectedJobRoot
        )
        {
            if (record == null || record.SchemaVersion != 1 || !ValidTimestamp(record.QueuedUtc))
                throw new InvalidDataException("publish_queue_request_invalid");
            var request = new PublishJobRequest
            {
                PlanId = record.PlanId,
                ManifestPath = record.ManifestPath,
                ManifestSha256 = record.ManifestSha256,
                StagedDrawing = record.StagedDrawing,
                OutputDirectory = record.OutputDirectory,
                SheetCount = record.SheetCount,
            };
            var validation = PublishJobValidator.Validate(request, _trustedWorkspaceRoot)
                ?? PublishManifestReader.Validate(request);
            if (validation != null)
                throw new InvalidDataException("publish_queue_request_" + validation);
            if (!SamePath(Path.GetDirectoryName(Path.GetFullPath(request.ManifestPath)), expectedJobRoot))
                throw new InvalidDataException("publish_queue_job_mismatch");
            return request;
        }

        private static void ValidateStartedRecord(
            PublishQueueStartedRecord record,
            PublishJobRequest request
        )
        {
            if (record == null
                || record.SchemaVersion != 1
                || !String.Equals(record.PlanId, request.PlanId, StringComparison.Ordinal)
                || !String.Equals(
                    record.ManifestSha256,
                    request.ManifestSha256,
                    StringComparison.Ordinal
                )
                || !ValidTimestamp(record.StartedUtc))
                throw new InvalidDataException("publish_queue_started_invalid");
        }

        private static PublishJobSnapshot ReadReceiptSnapshot(
            string receiptPath,
            PublishJobRequest request
        )
        {
            var receipt = ReadRecord<PublishReceipt>(receiptPath);
            if (receipt == null
                || receipt.SchemaVersion != 1
                || !String.Equals(receipt.PlanId, request.PlanId, StringComparison.Ordinal)
                || !String.Equals(
                    receipt.ManifestSha256,
                    request.ManifestSha256,
                    StringComparison.Ordinal
                )
                || !ValidTimestamp(receipt.CompletedUtc)
                || !(String.Equals(receipt.State, "succeeded", StringComparison.Ordinal)
                    || String.Equals(receipt.State, "failed", StringComparison.Ordinal))
                || (String.Equals(receipt.State, "succeeded", StringComparison.Ordinal)
                    && receipt.Error != null)
                || (String.Equals(receipt.State, "failed", StringComparison.Ordinal)
                    && (String.IsNullOrWhiteSpace(receipt.Error)
                        || !SafeErrorPattern.IsMatch(receipt.Error))))
                throw new InvalidDataException("publish_queue_receipt_invalid");
            return new PublishJobSnapshot
            {
                PlanId = request.PlanId,
                State = String.Equals(receipt.State, "succeeded", StringComparison.Ordinal)
                    ? PublishJobState.Succeeded
                    : PublishJobState.Failed,
                Error = receipt.Error,
            };
        }

        private static T ReadRecord<T>(string path)
        {
            try
            {
                using (var stream = new FileStream(
                    path,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.Read
                ))
                {
                    if (stream.Length < 2 || stream.Length > MaxRecordBytes)
                        throw new InvalidDataException("publish_queue_record_size_invalid");
                    return (T)new DataContractJsonSerializer(typeof(T)).ReadObject(stream);
                }
            }
            catch (SerializationException exception)
            {
                throw new InvalidDataException("publish_queue_record_invalid", exception);
            }
        }

        private static void WriteAtomicCreate<T>(string finalPath, T value)
        {
            var directory = Path.GetDirectoryName(finalPath);
            var temporaryPath = Path.Combine(
                directory,
                ".cadplot-queue-" + Guid.NewGuid().ToString("N") + ".tmp"
            );
            try
            {
                using (var stream = new FileStream(
                    temporaryPath,
                    FileMode.CreateNew,
                    FileAccess.Write,
                    FileShare.None
                ))
                {
                    new DataContractJsonSerializer(typeof(T)).WriteObject(stream, value);
                    stream.Flush(true);
                }
                File.Move(temporaryPath, finalPath);
                temporaryPath = null;
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

        private static void RequirePlainFile(string path)
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("publish_queue_record_redirected");
        }

        private static bool SameRequest(PublishJobRequest left, PublishJobRequest right)
        {
            return String.Equals(left.PlanId, right.PlanId, StringComparison.Ordinal)
                && String.Equals(left.ManifestSha256, right.ManifestSha256, StringComparison.Ordinal)
                && SamePath(left.ManifestPath, right.ManifestPath)
                && SamePath(left.StagedDrawing, right.StagedDrawing)
                && SamePath(left.OutputDirectory, right.OutputDirectory)
                && left.SheetCount == right.SheetCount;
        }

        private static bool SamePath(string left, string right)
        {
            return String.Equals(
                Path.GetFullPath(left).TrimEnd(
                    Path.DirectorySeparatorChar,
                    Path.AltDirectorySeparatorChar
                ),
                Path.GetFullPath(right).TrimEnd(
                    Path.DirectorySeparatorChar,
                    Path.AltDirectorySeparatorChar
                ),
                StringComparison.OrdinalIgnoreCase
            );
        }

        private static bool ValidTimestamp(string value)
        {
            DateTime parsed;
            return !String.IsNullOrWhiteSpace(value)
                && DateTime.TryParse(
                    value,
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.RoundtripKind,
                    out parsed
                );
        }

        private static bool IsBoundedFileException(Exception exception)
        {
            return exception is IOException
                || exception is UnauthorizedAccessException
                || exception is SecurityException;
        }
    }
}
