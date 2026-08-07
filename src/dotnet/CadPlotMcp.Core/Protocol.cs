using System;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;

namespace CadPlotMcp.Core
{
    public static class PipeProtocol
    {
        public const string Version = "1";
        public const string DefaultPipeName = "cadplot-mcp";
        public const string StatusCommand = "status";
        public const int MaxLineCharacters = 65536;
    }

    [DataContract]
    public sealed class PipeRequest
    {
        [DataMember(Name = "id", EmitDefaultValue = false)] public string Id { get; set; }
        [DataMember(Name = "version")] public string Version { get; set; }
        [DataMember(Name = "command")] public string Command { get; set; }
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
            // Deliberately a positive whitelist. No drawing mutation can cross this boundary.
            if (!String.Equals(request.Command, PipeProtocol.StatusCommand, StringComparison.Ordinal))
            {
                response.Error = "command_not_allowed";
                return response;
            }
            response.Ok = true;
            response.Product = _productName();
            response.Adapter = _adapter;
            response.ReadOnly = true;
            return response;
        }
    }
}
