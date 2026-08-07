using System;

namespace CadPlotMcp.Core
{
    public sealed class ViewportGeometryResult
    {
        public double PaperWidthUnits { get; set; }
        public double PaperHeightUnits { get; set; }
        public double ModelCenterX { get; set; }
        public double ModelCenterY { get; set; }
        public double CustomScale { get; set; }
        public bool QuarterTurn { get; set; }
    }

    public static class ViewportGeometryCalculator
    {
        public static ViewportGeometryResult Calculate(
            PublishPlotGeometry geometry,
            double effectivePaperWidthMillimetres,
            double effectivePaperHeightMillimetres,
            double paperUnitMillimetres
        )
        {
            if (geometry == null || geometry.Window == null)
                throw new ArgumentNullException("geometry");
            if (!Positive(effectivePaperWidthMillimetres)
                || !Positive(effectivePaperHeightMillimetres)
                || !Positive(paperUnitMillimetres)
                || !Positive(geometry.DrawingUnitMillimetres)
                || !Positive(geometry.ScaleDenominator))
                throw new ArgumentOutOfRangeException("geometry");

            var windowWidth = geometry.Window.MaxX - geometry.Window.MinX;
            var windowHeight = geometry.Window.MaxY - geometry.Window.MinY;
            if (!Positive(windowWidth) || !Positive(windowHeight))
                throw new ArgumentOutOfRangeException("geometry");

            var paperWidth = effectivePaperWidthMillimetres / paperUnitMillimetres;
            var paperHeight = effectivePaperHeightMillimetres / paperUnitMillimetres;
            return new ViewportGeometryResult
            {
                PaperWidthUnits = paperWidth,
                PaperHeightUnits = paperHeight,
                ModelCenterX = (geometry.Window.MinX + geometry.Window.MaxX) / 2.0,
                ModelCenterY = (geometry.Window.MinY + geometry.Window.MaxY) / 2.0,
                CustomScale = geometry.DrawingUnitMillimetres
                    / geometry.ScaleDenominator
                    / paperUnitMillimetres,
                QuarterTurn = (windowWidth > windowHeight) != (paperWidth > paperHeight),
            };
        }

        private static bool Positive(double value)
        {
            return value > 0 && !Double.IsNaN(value) && !Double.IsInfinity(value);
        }
    }
}
