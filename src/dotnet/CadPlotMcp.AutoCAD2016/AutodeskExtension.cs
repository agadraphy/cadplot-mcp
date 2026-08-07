using Autodesk.AutoCAD.Runtime;

[assembly: ExtensionApplication(typeof(CadPlotMcp.AutoCAD2016.AutodeskExtension))]
namespace CadPlotMcp.AutoCAD2016
{
    public sealed class AutodeskExtension : IExtensionApplication
    {
        public void Initialize() { Plugin.Start(); }
        public void Terminate() { Plugin.Stop(); }
    }
}
