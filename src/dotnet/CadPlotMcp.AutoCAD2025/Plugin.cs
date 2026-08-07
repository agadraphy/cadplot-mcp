using System;
#if AUTOCAD_SDK
using CadPlotMcp.AutoCAD;
#endif
using CadPlotMcp.Core;

namespace CadPlotMcp.AutoCAD2025;

public sealed class Plugin
{
#if AUTOCAD_SDK
    private static AutoCadPublishRuntime? _runtime;
#else
    private static NamedPipeCommandHost? _host;
#endif

    public static void Start()
    {
#if AUTOCAD_SDK
        if (_runtime is not null) return;
        _runtime = new AutoCadPublishRuntime(
            "autocad-2025-net8",
            () => "AutoCAD 2025-2026"
        );
#else
        if (_host is not null) return;
        _host = new NamedPipeCommandHost(
            null,
            new CommandDispatcher(
                "autocad-2025-net8",
                () => "AutoCAD 2025",
                Environment.GetEnvironmentVariable("CADPLOT_WORKSPACE_ROOT")
            )
        );
        _host.Start();
#endif
    }

    public static void Stop()
    {
#if AUTOCAD_SDK
        _runtime?.Dispose();
        _runtime = null;
#else
        _host?.Dispose();
        _host = null;
#endif
    }
}
