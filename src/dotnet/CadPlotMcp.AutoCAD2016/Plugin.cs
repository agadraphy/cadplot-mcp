using System;
using CadPlotMcp.Core;

namespace CadPlotMcp.AutoCAD2016
{
    public sealed class Plugin
    {
        private static NamedPipeCommandHost _host;
        public static void Start()
        {
            if (_host != null) return;
            _host = new NamedPipeCommandHost(
                null,
                new CommandDispatcher(
                    "autocad-2016-net45",
                    () => "AutoCAD 2016",
                    Environment.GetEnvironmentVariable("CADPLOT_WORKSPACE_ROOT")
                )
            );
            _host.Start();
        }
        public static void Stop() { if (_host != null) { _host.Dispose(); _host = null; } }
    }
}
