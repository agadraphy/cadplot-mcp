using CadPlotMcp.Core;

namespace CadPlotMcp.AutoCAD2025;

public sealed class Plugin
{
    private static NamedPipeCommandHost? _host;

    public static void Start()
    {
        if (_host is not null) return;
        _host = new NamedPipeCommandHost(null, new CommandDispatcher("autocad-2025-net8", () => "AutoCAD 2025"));
        _host.Start();
    }

    public static void Stop()
    {
        _host?.Dispose();
        _host = null;
    }
}
