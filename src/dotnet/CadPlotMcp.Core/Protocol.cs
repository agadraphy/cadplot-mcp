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
        [DataMember(Name = "readOnly", EmitDefaultValue = false)] public bool ReadOnly { get; set; }
        [DataMember(Name = "plan_id", EmitDefaultValue = false)] public string PlanId { get; set; }
        [DataMember(Name = "acceptedSheetCount", EmitDefaultValue = false)] public int AcceptedSheetCount { get; set; }
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

        public CommandDispatcher(string adapter, Func<string> productName)
        {
            _adapter = adapter ?? "unknown";
            _productName = productName ?? (() => "AutoCAD");
        }

        public PipeResponse Dispatch(PipeRequest request)
        {
            var response = new PipeResponse { Id = request == null ? null : request.Id, Version = PipeProtocol.Version };
            if (request == null || request.Version != PipeProtocol.Version)
            {
                response.Error = "unsupported_protocol_version";
                return response;
            }
            // Deliberately a positive whitelist. Neither command can mutate a drawing.
            if (String.Equals(request.Command, PipeProtocol.StatusCommand, StringComparison.Ordinal))
            {
                response.Ok = true;
                response.Product = _productName();
                response.Adapter = _adapter;
                response.ReadOnly = true;
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
            else
            {
                response.Error = "command_not_allowed";
                return response;
            }
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
