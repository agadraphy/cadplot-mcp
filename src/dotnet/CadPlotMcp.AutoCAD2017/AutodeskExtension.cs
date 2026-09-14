using Autodesk.AutoCAD.Runtime;

[assembly: ExtensionApplication(typeof(CadPlotMcp.AutoCAD2017.AutodeskExtension))]
namespace CadPlotMcp.AutoCAD2017
{
    public sealed class AutodeskExtension : IExtensionApplication
    {
        public void Initialize() { Plugin.Start(); }
        public void Terminate() { Plugin.Stop(); }
    }
}
