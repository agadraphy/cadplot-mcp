using Autodesk.AutoCAD.Runtime;

[assembly: ExtensionApplication(typeof(CadPlotMcp.AutoCAD2025.AutodeskExtension))]
namespace CadPlotMcp.AutoCAD2025;

public sealed class AutodeskExtension : IExtensionApplication
{
    public void Initialize() => Plugin.Start();
    public void Terminate() => Plugin.Stop();
}
