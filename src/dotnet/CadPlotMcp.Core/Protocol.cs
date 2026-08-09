using System;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Text.RegularExpressions;

namespace CadPlotMcp.Core
{
    public static class PipeProtocol
    {
        public const string Version = "1";
        public const string DefaultPipeName = "cadplot-mcp";
        public const string StatusCommand = "status";
        public const string PreviewPublishPlanCommand = "preview_publish_plan";
        public const string ValidateStagedJobCommand = "validate_staged_job";
        public const string QueuePublishJobCommand = "queue_publish_job";
        public const string PublishJobStatusCommand = "publish_job_status";
        public const int MaxLineCharacters = 65536;
    }

    [DataContract]
    public sealed class PipeRequest
    {
        [DataMember(Name = "id", EmitDefaultValue = false)] public string Id { get; set; }
        [DataMember(Name = "version")] public string Version { get; set; }
        [DataMember(Name = "command")] public string Command { get; set; }
        [DataMember(Name = "plan_id", EmitDefaultValue = false)] public string PlanId { get; set; }
        [DataMember(Name = "drawing", EmitDefaultValue = false)] public string Drawing { get; set; }
        [DataMember(Name = "sheet_count", EmitDefaultValue = false)] public int SheetCount { get; set; }
        [DataMember(Name = "manifest_path", EmitDefaultValue = false)] public string ManifestPath { get; set; }
        [DataMember(Name = "manifest_sha256", EmitDefaultValue = false)] public string ManifestSha256 { get; set; }
        [DataMember(Name = "output_directory", EmitDefaultValue = false)] public string OutputDirectory { get; set; }
    }

    [DataContract]
    public sealed class PipeResponse
    {
        [DataMember(Name = "id", EmitDefaultValue = false)] public string Id { get; set; }
        [DataMember(Name = "ok")] public bool Ok { get; set; }
        [DataMember(Name = "version")] public string Version { get; set; }
        [DataMember(Name = "error", EmitDefaultValue = false)] public string Error { get; set; }
        [DataMember(Name = "product", EmitDefaultValue = false)] public string Product { get; set; }
        [DataMember(Name = "adapter", EmitDefaultValue = false)] public string Adapter { get; set; }
        [DataMember(Name = "buildCommit", EmitDefaultValue = false)] public string BuildCommit { get; set; }
        [DataMember(Name = "pluginSha256", EmitDefaultValue = false)] public string PluginSha256 { get; set; }
        [DataMember(Name = "runtimeSeries", EmitDefaultValue = false)] public string RuntimeSeries { get; set; }
        [DataMember(Name = "runtimeSupported", EmitDefaultValue = false)] public bool? RuntimeSupported { get; set; }
        [DataMember(Name = "readOnly", EmitDefaultValue = false)] public bool ReadOnly { get; set; }
        [DataMember(Name = "plan_id", EmitDefaultValue = false)] public string PlanId { get; set; }
        [DataMember(Name = "acceptedSheetCount", EmitDefaultValue = false)] public int AcceptedSheetCount { get; set; }
        [DataMember(Name = "workspaceConfigured", EmitDefaultValue = false)] public bool? WorkspaceConfigured { get; set; }
        [DataMember(Name = "publishEnabled", EmitDefaultValue = false)] public bool? PublishEnabled { get; set; }
        [DataMember(Name = "jobState", EmitDefaultValue = false)] public string JobState { get; set; }
        [DataMember(Name = "jobError", EmitDefaultValue = false)] public string JobError { get; set; }
        [DataMember(Name = "queueCapacity", EmitDefaultValue = false)] public int? QueueCapacity { get; set; }
        [DataMember(Name = "queuePending", EmitDefaultValue = false)] public int? QueuePending { get; set; }
        [DataMember(Name = "queueRunning", EmitDefaultValue = false)] public int? QueueRunning { get; set; }
        [DataMember(Name = "queueAvailable", EmitDefaultValue = false)] public int? QueueAvailable { get; set; }
        [DataMember(Name = "queueRecoveredOnStartup", EmitDefaultValue = false)] public int? QueueRecoveredOnStartup { get; set; }
        [DataMember(Name = "queueInterruptedOnStartup", EmitDefaultValue = false)] public int? QueueInterruptedOnStartup { get; set; }
        [DataMember(Name = "publishInitializationError", EmitDefaultValue = false)] public string PublishInitializationError { get; set; }
    }

    public static class JsonLineCodec
    {
        public static string ReadLimitedLine(TextReader reader)
        {
            if (reader == null) throw new ArgumentNullException("reader");
            var value = new StringBuilder();
            while (value.Length <= PipeProtocol.MaxLineCharacters)
            {
                var next = reader.Read();
                if (next < 0 || next == '\n') return value.ToString().TrimEnd('\r');
                value.Append((char)next);
            }
            throw new SerializationException("Request exceeded the size limit.");
        }

        public static PipeRequest ReadRequest(string line)
        {
            if (String.IsNullOrWhiteSpace(line)) throw new SerializationException("Request is empty.");
            using (var stream = new MemoryStream(Encoding.UTF8.GetBytes(line)))
                return (PipeRequest)new DataContractJsonSerializer(typeof(PipeRequest)).ReadObject(stream);
        }

        public static string WriteResponse(PipeResponse response)
        {
            using (var stream = new MemoryStream())
            {
                new DataContractJsonSerializer(typeof(PipeResponse)).WriteObject(stream, response);
                return Encoding.UTF8.GetString(stream.ToArray());
            }
        }
    }

    public sealed class CommandDispatcher
    {
        private static readonly Regex PlanIdPattern = new Regex(
            "^sha256:[0-9a-f]{64}$",
            RegexOptions.CultureInvariant
        );
        private readonly string _adapter;
        private readonly Func<string> _productName;
        private readonly string _trustedWorkspaceRoot;
        private readonly PublishJobQueue _publishQueue;
        private readonly bool _publishEnabled;
        private readonly string _buildCommit;
        private readonly string _pluginSha256;
        private readonly string _publishInitializationError;
        private readonly string _runtimeSeries;
        private readonly bool _runtimeSupported;

        public CommandDispatcher(
            string adapter,
            Func<string> productName,
            string trustedWorkspaceRoot = null,
            PublishJobQueue publishQueue = null,
            bool publishEnabled = false,
            string buildCommit = null,
            string pluginSha256 = null,
            string runtimeSeries = null,
            bool runtimeSupported = true,
            string publishInitializationError = null
        )
        {
            _adapter = adapter ?? "unknown";
            _productName = productName ?? (() => "AutoCAD");
            _trustedWorkspaceRoot = trustedWorkspaceRoot;
            _publishQueue = publishQueue;
            _runtimeSeries = runtimeSeries;
            _runtimeSupported = runtimeSupported;
            _publishEnabled = runtimeSupported && publishEnabled && publishQueue != null;
            _buildCommit = buildCommit;
            _pluginSha256 = pluginSha256;
            _publishInitializationError = publishInitializationError;
        }

        public PipeResponse Dispatch(PipeRequest request)
        {
            var response = new PipeResponse { Id = request == null ? null : request.Id, Version = PipeProtocol.Version };
            if (request == null || request.Version != PipeProtocol.Version)
            {
                response.Error = "unsupported_protocol_version";
                return response;
            }
            // Deliberately a positive whitelist. Write capability remains separately opt-in.
            if (String.Equals(request.Command, PipeProtocol.StatusCommand, StringComparison.Ordinal))
            {
                response.Ok = true;
                response.Product = _productName();
                response.Adapter = _adapter;
                response.BuildCommit = _buildCommit;
                response.PluginSha256 = _pluginSha256;
                response.RuntimeSeries = _runtimeSeries;
                response.RuntimeSupported = _runtimeSupported;
                response.ReadOnly = true;
                response.WorkspaceConfigured = !String.IsNullOrWhiteSpace(_trustedWorkspaceRoot);
                response.PublishEnabled = _publishEnabled;
                response.PublishInitializationError = _publishInitializationError;
                PopulateQueueTelemetry(response);
                return response;
            }
            if (String.Equals(request.Command, PipeProtocol.PreviewPublishPlanCommand, StringComparison.Ordinal))
            {
                if (!IsValidPreview(request))
                {
                    response.Error = "invalid_publish_plan_preview";
                    return response;
                }
                response.Ok = true;
                response.Product = _productName();
                response.Adapter = _adapter;
                response.ReadOnly = true;
                response.PlanId = request.PlanId;
                response.AcceptedSheetCount = request.SheetCount;
                return response;
            }
            if (String.Equals(request.Command, PipeProtocol.ValidateStagedJobCommand, StringComparison.Ordinal))
            {
                if (String.IsNullOrWhiteSpace(_trustedWorkspaceRoot))
                {
                    response.Error = "workspace_not_configured";
                    return response;
                }
                var job = CreateJob(request);
                string error;
                try
                {
                    error = PublishJobValidator.Validate(job, _trustedWorkspaceRoot)
                        ?? PublishManifestReader.Validate(job);
                }
                catch (Exception exception)
                {
                    if (exception is IOException
                        || exception is UnauthorizedAccessException
                        || exception is System.Security.SecurityException)
                        error = "job_files_unavailable";
                    else
                        throw;
                }
                if (error != null)
                {
                    response.Error = error;
                    return response;
                }
                response.Ok = true;
                response.Product = _productName();
                response.Adapter = _adapter;
                response.ReadOnly = true;
                response.PlanId = request.PlanId;
                response.AcceptedSheetCount = request.SheetCount;
                response.WorkspaceConfigured = true;
                return response;
            }
            if (String.Equals(request.Command, PipeProtocol.QueuePublishJobCommand, StringComparison.Ordinal))
            {
                if (!_runtimeSupported)
                {
                    response.Error = "unsupported_autocad_runtime";
                    return response;
                }
                if (!_publishEnabled)
                {
                    response.Error = "publish_disabled";
                    return response;
                }
                var job = CreateJob(request);
                string error;
                if (!_publishQueue.TryEnqueue(job, out error))
                {
                    response.Error = error;
                    PopulateQueueTelemetry(response);
                    return response;
                }
                response.Ok = true;
                response.Product = _productName();
                response.Adapter = _adapter;
                response.ReadOnly = false;
                response.PublishEnabled = true;
                response.PlanId = request.PlanId;
                response.AcceptedSheetCount = request.SheetCount;
                response.JobState = PublishJobState.Pending.ToString();
                PopulateQueueTelemetry(response);
                return response;
            }
            if (String.Equals(request.Command, PipeProtocol.PublishJobStatusCommand, StringComparison.Ordinal))
            {
                if (!_runtimeSupported)
                {
                    response.Error = "unsupported_autocad_runtime";
                    return response;
                }
                if (!_publishEnabled)
                {
                    response.Error = "publish_disabled";
                    return response;
                }
                if (String.IsNullOrWhiteSpace(request.PlanId) || !PlanIdPattern.IsMatch(request.PlanId))
                {
                    response.Error = "invalid_plan_id";
                    return response;
                }
                var snapshot = _publishQueue.GetStatus(request.PlanId);
                if (snapshot == null)
                {
                    response.Error = "job_not_found";
                    return response;
                }
                response.Ok = true;
                response.Product = _productName();
                response.Adapter = _adapter;
                response.ReadOnly = true;
                response.PublishEnabled = true;
                response.PlanId = snapshot.PlanId;
                response.JobState = snapshot.State.ToString();
                response.JobError = snapshot.Error;
                PopulateQueueTelemetry(response);
                return response;
            }
            else
            {
                response.Error = "command_not_allowed";
                return response;
            }
        }

        private void PopulateQueueTelemetry(PipeResponse response)
        {
            if (_publishQueue == null) return;
            var telemetry = _publishQueue.GetTelemetry();
            response.QueueCapacity = telemetry.Capacity;
            response.QueuePending = telemetry.Pending;
            response.QueueRunning = telemetry.Running;
            response.QueueAvailable = telemetry.Available;
            response.QueueRecoveredOnStartup = telemetry.RecoveredOnStartup;
            response.QueueInterruptedOnStartup = telemetry.InterruptedOnStartup;
        }

        private static PublishJobRequest CreateJob(PipeRequest request)
        {
            return new PublishJobRequest
            {
                PlanId = request.PlanId,
                ManifestPath = request.ManifestPath,
                ManifestSha256 = request.ManifestSha256,
                StagedDrawing = request.Drawing,
                OutputDirectory = request.OutputDirectory,
                SheetCount = request.SheetCount,
            };
        }

        private static bool IsValidPreview(PipeRequest request)
        {
            if (String.IsNullOrWhiteSpace(request.PlanId) || !PlanIdPattern.IsMatch(request.PlanId))
                return false;
            if (String.IsNullOrWhiteSpace(request.Drawing) || !Path.IsPathRooted(request.Drawing))
                return false;
            if (!String.Equals(Path.GetExtension(request.Drawing), ".dwg", StringComparison.OrdinalIgnoreCase))
                return false;
            return request.SheetCount >= 1 && request.SheetCount <= 5000;
        }
    }
}
