using System;
#if AUTOCAD_SDK
using CadPlotMcp.AutoCAD;
#endif
using CadPlotMcp.Core;

namespace CadPlotMcp.AutoCAD2016
{
    public sealed class Plugin
    {
#if AUTOCAD_SDK
        private static AutoCadPublishRuntime _runtime;
#else
        private static NamedPipeCommandHost _host;
#endif
        public static void Start()
        {
#if AUTOCAD_SDK
            if (_runtime != null) return;
            _runtime = new AutoCadPublishRuntime(
                "autocad-2016-net45",
                () => "AutoCAD 2016"
            );
#else
            if (_host != null) return;
            _host = new NamedPipeCommandHost(
                Environment.GetEnvironmentVariable("CADPLOT_PIPE_NAME"),
                new CommandDispatcher(
                    "autocad-2016-net45",
                    () => "AutoCAD 2016",
                    Environment.GetEnvironmentVariable("CADPLOT_WORKSPACE_ROOT")
                )
            );
            _host.Start();
#endif
        }
        public static void Stop()
        {
#if AUTOCAD_SDK
            if (_runtime != null) { _runtime.Dispose(); _runtime = null; }
#else
            if (_host != null) { _host.Dispose(); _host = null; }
#endif
        }
    }
}
