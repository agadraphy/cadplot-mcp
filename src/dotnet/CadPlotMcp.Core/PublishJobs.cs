using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Security;
using System.Text.RegularExpressions;

namespace CadPlotMcp.Core
{
    public enum PublishJobState
    {
        Pending,
        Running,
        Succeeded,
        Failed,
        Cancelled,
    }

    public sealed class PublishJobRequest
    {
        public string PlanId { get; set; }
        public string ManifestPath { get; set; }
        public string ManifestSha256 { get; set; }
        public string StagedDrawing { get; set; }
        public string OutputDirectory { get; set; }
        public int SheetCount { get; set; }
    }

    public sealed class PublishJobSnapshot
    {
        public string PlanId { get; set; }
        public PublishJobState State { get; set; }
        public string Error { get; set; }
    }

    public sealed class PublishQueueTelemetry
    {
        public int Capacity { get; set; }
        public int Pending { get; set; }
        public int Running { get; set; }
        public int Available { get; set; }
        public int RecoveredOnStartup { get; set; }
        public int InterruptedOnStartup { get; set; }
        public int CancelledOnStartup { get; set; }
        public string Authentication { get; set; }
    }

    public static class PublishJobValidator
    {
        private static readonly Regex PlanIdPattern = new Regex(
            "^sha256:[0-9a-f]{64}$",
            RegexOptions.CultureInvariant
        );
        private static readonly Regex Sha256Pattern = new Regex(
            "^[0-9a-f]{64}$",
            RegexOptions.CultureInvariant
        );
        private static readonly Regex JobIdPattern = new Regex(
            "^job-[0-9]{8}T(?:[0-9]{6}|[0-9]{12})Z-[0-9a-f]{12}$",
            RegexOptions.CultureInvariant
        );

        public static string Validate(PublishJobRequest request, string trustedWorkspaceRoot)
        {
            if (request == null) return "request_required";
            if (String.IsNullOrWhiteSpace(request.PlanId) || !PlanIdPattern.IsMatch(request.PlanId))
                return "invalid_plan_id";
            if (request.SheetCount < 1 || request.SheetCount > 5000)
                return "invalid_sheet_count";
            if (String.IsNullOrWhiteSpace(request.ManifestSha256)
                || !Sha256Pattern.IsMatch(request.ManifestSha256))
                return "invalid_manifest_sha256";

            string manifest;
            string drawing;
            string output;
            string workspace;
            try
            {
                manifest = Path.GetFullPath(request.ManifestPath ?? String.Empty);
                drawing = Path.GetFullPath(request.StagedDrawing ?? String.Empty);
                output = Path.GetFullPath(request.OutputDirectory ?? String.Empty);
                workspace = Path.GetFullPath(trustedWorkspaceRoot ?? String.Empty);
            }
            catch (Exception exception)
            {
                if (exception is ArgumentException || exception is NotSupportedException || exception is PathTooLongException)
                    return "invalid_job_path";
                throw;
            }

            if (!String.Equals(Path.GetFileName(manifest), "manifest.json", StringComparison.OrdinalIgnoreCase))
                return "invalid_manifest_path";
            var jobRoot = Path.GetDirectoryName(manifest);
            if (String.IsNullOrWhiteSpace(jobRoot)) return "invalid_job_root";
            if (!JobIdPattern.IsMatch(Path.GetFileName(jobRoot))) return "invalid_job_id";
            if (!SamePath(Path.GetDirectoryName(jobRoot), workspace)) return "job_outside_workspace";
            var expectedSource = Path.Combine(jobRoot, "source");
            var expectedOutput = Path.Combine(jobRoot, "output");
            if (!IsInside(drawing, expectedSource) || !String.Equals(Path.GetExtension(drawing), ".dwg", StringComparison.OrdinalIgnoreCase))
                return "drawing_outside_job";
            if (!SamePath(output, expectedOutput)) return "output_outside_job";
            if (!File.Exists(manifest) || !File.Exists(drawing) || !Directory.Exists(output))
                return "job_files_missing";
            if (HasReparsePoint(workspace, workspace)
                || HasReparsePoint(jobRoot, workspace)
                || HasReparsePoint(expectedSource, workspace)
                || HasReparsePoint(output, workspace)
                || HasReparsePoint(manifest, workspace)
                || HasReparsePoint(drawing, workspace))
                return "job_path_redirected";
            if (!String.Equals(
                FileSha256.Compute(manifest),
                request.ManifestSha256,
                StringComparison.Ordinal
            )) return "manifest_changed";
            return null;
        }

        private static bool IsInside(string candidate, string root)
        {
            var normalizedCandidate = WithTrailingSeparator(Path.GetFullPath(candidate));
            var normalizedRoot = WithTrailingSeparator(Path.GetFullPath(root));
            return normalizedCandidate.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase);
        }

        private static bool SamePath(string left, string right)
        {
            return String.Equals(
                Path.GetFullPath(left).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                Path.GetFullPath(right).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                StringComparison.OrdinalIgnoreCase
            );
        }

        private static string WithTrailingSeparator(string value)
        {
            return value.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
        }

        private static bool HasReparsePoint(string candidate, string root)
        {
            var current = Path.GetFullPath(candidate)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            var boundary = Path.GetFullPath(root)
                .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (!String.Equals(current, boundary, StringComparison.OrdinalIgnoreCase)
                && !WithTrailingSeparator(current).StartsWith(
                    WithTrailingSeparator(boundary),
                    StringComparison.OrdinalIgnoreCase
                )) return true;

            while (true)
            {
                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    return true;
                if (String.Equals(current, boundary, StringComparison.OrdinalIgnoreCase))
                    return false;
                current = Path.GetDirectoryName(current);
                if (String.IsNullOrWhiteSpace(current)) return true;
            }
        }
    }

    public static class FileSha256
    {
        public static string Compute(string path)
        {
            using (var algorithm = SHA256.Create())
            using (var stream = File.OpenRead(path))
            {
                var bytes = algorithm.ComputeHash(stream);
                return BitConverter.ToString(bytes).Replace("-", String.Empty).ToLowerInvariant();
            }
        }
    }

    public sealed class PublishJobQueue
    {
        private readonly object _gate = new object();
        private readonly Queue<PublishJobRequest> _pending = new Queue<PublishJobRequest>();
        private readonly Dictionary<string, PublishJobSnapshot> _states =
            new Dictionary<string, PublishJobSnapshot>(StringComparer.Ordinal);
        private readonly Dictionary<string, string> _manifestHashes =
            new Dictionary<string, string>(StringComparer.Ordinal);
        private readonly int _capacity;
        private readonly string _trustedWorkspaceRoot;
        private readonly PublishQueueJournal _journal;
        private readonly int _recoveredOnStartup;
        private readonly int _interruptedOnStartup;
        private readonly int _cancelledOnStartup;

        public PublishJobQueue(string trustedWorkspaceRoot, int capacity)
            : this(trustedWorkspaceRoot, capacity, PublishQueueKeyStore.DefaultPath())
        {
        }

        public PublishJobQueue(string trustedWorkspaceRoot, int capacity, string authenticationKeyPath)
        {
            if (String.IsNullOrWhiteSpace(trustedWorkspaceRoot))
                throw new ArgumentException("A trusted workspace root is required.", "trustedWorkspaceRoot");
            if (capacity < 1 || capacity > 1000) throw new ArgumentOutOfRangeException("capacity");
            _trustedWorkspaceRoot = Path.GetFullPath(trustedWorkspaceRoot);
            if (!Directory.Exists(_trustedWorkspaceRoot))
                throw new ArgumentException("The trusted workspace root must exist.", "trustedWorkspaceRoot");
            if ((File.GetAttributes(_trustedWorkspaceRoot) & FileAttributes.ReparsePoint) != 0)
                throw new ArgumentException("The trusted workspace root cannot be redirected.", "trustedWorkspaceRoot");
            _capacity = capacity;
            var authenticationKey = PublishQueueKeyStore.LoadOrCreate(
                authenticationKeyPath,
                _trustedWorkspaceRoot
            );
            try { _journal = new PublishQueueJournal(_trustedWorkspaceRoot, authenticationKey); }
            finally { Array.Clear(authenticationKey, 0, authenticationKey.Length); }
            var recovery = _journal.Recover(capacity);
            foreach (var request in recovery.Pending) _pending.Enqueue(request);
            foreach (var pair in recovery.States) _states.Add(pair.Key, pair.Value);
            foreach (var pair in recovery.ManifestHashes) _manifestHashes.Add(pair.Key, pair.Value);
            _recoveredOnStartup = recovery.RecoveredOnStartup;
            _interruptedOnStartup = recovery.InterruptedOnStartup;
            _cancelledOnStartup = recovery.CancelledOnStartup;
        }

        public int PendingCount
        {
            get { lock (_gate) return _pending.Count; }
        }

        public int Capacity
        {
            get { return _capacity; }
        }

        public int AvailableCount
        {
            get { return GetTelemetry().Available; }
        }

        public int RunningCount
        {
            get { return GetTelemetry().Running; }
        }

        public PublishQueueTelemetry GetTelemetry()
        {
            lock (_gate)
            {
                var running = 0;
                foreach (var snapshot in _states.Values)
                    if (snapshot.State == PublishJobState.Running) running++;
                return new PublishQueueTelemetry
                {
                    Capacity = _capacity,
                    Pending = _pending.Count,
                    Running = running,
                    Available = _capacity - _pending.Count,
                    RecoveredOnStartup = _recoveredOnStartup,
                    InterruptedOnStartup = _interruptedOnStartup,
                    CancelledOnStartup = _cancelledOnStartup,
                    Authentication = PublishQueueJournal.AuthenticationScheme,
                };
            }
        }

        public bool TryEnqueue(PublishJobRequest request, out string error)
        {
            try
            {
                error = PublishJobValidator.Validate(request, _trustedWorkspaceRoot);
                if (error != null) return false;
                error = PublishManifestReader.Validate(request);
                if (error != null) return false;
            }
            catch (Exception exception)
            {
                if (exception is IOException
                    || exception is UnauthorizedAccessException
                    || exception is SecurityException)
                {
                    error = "job_files_unavailable";
                    return false;
                }
                throw;
            }
            lock (_gate)
            {
                if (_pending.Count >= _capacity)
                {
                    error = "queue_full";
                    return false;
                }
                if (_states.ContainsKey(request.PlanId))
                {
                    error = "duplicate_plan";
                    return false;
                }
                error = _journal.RecordPending(request);
                if (error != null) return false;
                _pending.Enqueue(request);
                _states.Add(
                    request.PlanId,
                    new PublishJobSnapshot { PlanId = request.PlanId, State = PublishJobState.Pending }
                );
                _manifestHashes.Add(request.PlanId, request.ManifestSha256);
                return true;
            }
        }

        public bool TryCancelPending(string planId, string manifestSha256, out string error)
        {
            lock (_gate)
            {
                PublishJobSnapshot snapshot;
                string expectedManifestSha256;
                if (!_states.TryGetValue(planId ?? String.Empty, out snapshot)
                    || !_manifestHashes.TryGetValue(planId ?? String.Empty, out expectedManifestSha256))
                {
                    error = "job_not_found";
                    return false;
                }
                if (!String.Equals(
                    expectedManifestSha256,
                    manifestSha256,
                    StringComparison.Ordinal
                ))
                {
                    error = "manifest_mismatch";
                    return false;
                }
                if (snapshot.State == PublishJobState.Cancelled)
                {
                    error = null;
                    return true;
                }
                if (snapshot.State != PublishJobState.Pending)
                {
                    error = "job_not_pending";
                    return false;
                }

                PublishJobRequest pendingRequest = null;
                foreach (var candidate in _pending)
                {
                    if (String.Equals(candidate.PlanId, planId, StringComparison.Ordinal))
                    {
                        pendingRequest = candidate;
                        break;
                    }
                }
                if (pendingRequest == null)
                {
                    error = "queue_state_missing";
                    return false;
                }
                error = _journal.RecordCancelled(pendingRequest);
                if (error != null) return false;

                var retained = new Queue<PublishJobRequest>();
                var removed = false;
                while (_pending.Count > 0)
                {
                    var candidate = _pending.Dequeue();
                    if (!removed && String.Equals(candidate.PlanId, planId, StringComparison.Ordinal))
                        removed = true;
                    else
                        retained.Enqueue(candidate);
                }
                while (retained.Count > 0) _pending.Enqueue(retained.Dequeue());
                if (!removed)
                    throw new InvalidOperationException("Pending queue state changed while cancelling.");
                snapshot.State = PublishJobState.Cancelled;
                snapshot.Error = null;
                return true;
            }
        }

        public bool TryStartNext(out PublishJobRequest request, out string error)
        {
            lock (_gate)
            {
                if (_pending.Count == 0)
                {
                    request = null;
                    error = "no_pending_job";
                    return false;
                }
                request = _pending.Peek();
                error = _journal.RecordStarted(request);
                if (error != null)
                {
                    var blocked = _pending.Dequeue();
                    var snapshot = _states[blocked.PlanId];
                    snapshot.State = PublishJobState.Failed;
                    snapshot.Error = error;
                    request = null;
                    return false;
                }
                _pending.Dequeue();
                _states[request.PlanId].State = PublishJobState.Running;
                return true;
            }
        }

        public void Complete(string planId, bool succeeded, string error = null)
        {
            lock (_gate)
            {
                PublishJobSnapshot snapshot;
                if (!_states.TryGetValue(planId, out snapshot) || snapshot.State != PublishJobState.Running)
                    throw new InvalidOperationException("Only a running publish job can be completed.");
                snapshot.State = succeeded ? PublishJobState.Succeeded : PublishJobState.Failed;
                snapshot.Error = succeeded ? null : (error ?? "publish_failed");
            }
        }

        public PublishJobSnapshot GetStatus(string planId)
        {
            lock (_gate)
            {
                PublishJobSnapshot snapshot;
                if (!_states.TryGetValue(planId, out snapshot)) return null;
                return new PublishJobSnapshot
                {
                    PlanId = snapshot.PlanId,
                    State = snapshot.State,
                    Error = snapshot.Error,
                };
            }
        }
    }
}
