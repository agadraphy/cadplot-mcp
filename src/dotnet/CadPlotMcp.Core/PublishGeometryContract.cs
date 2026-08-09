using System;

namespace CadPlotMcp.Core
{
    /// <summary>
    /// Replays the manifest's scale/orientation math without AutoCAD so a
    /// changed or internally inconsistent geometry contract fails before plot.
    /// </summary>
    public static class PublishGeometryContract
    {
        private const double MaximumToleranceRatio = 0.1;
        private const double RoundedScaleTolerance = 0.00000051;

        public static string Validate(PublishPlotGeometry geometry)
        {
            if (geometry == null || geometry.Window == null)
                return "invalid_plot_geometry";
            if (geometry.RotationDegrees != 0 && geometry.RotationDegrees != 90)
                return "invalid_plot_rotation";
            if (!PositiveFinite(geometry.ScaleDenominator)
                || !PositiveFinite(geometry.DerivedScaleDenominator)
                || !PositiveFinite(geometry.DrawingUnitMillimetres)
                || !PositiveFinite(geometry.PaperWidthMillimetres)
                || !PositiveFinite(geometry.PaperHeightMillimetres)
                || !Finite(geometry.ScaleToleranceRatio)
                || geometry.ScaleToleranceRatio < 0
                || geometry.ScaleToleranceRatio > MaximumToleranceRatio)
                return "invalid_plot_scale";

            var window = geometry.Window;
            if (!Finite(window.MinX) || !Finite(window.MinY)
                || !Finite(window.MaxX) || !Finite(window.MaxY)
                || window.MaxX <= window.MinX || window.MaxY <= window.MinY)
                return "invalid_plot_window";

            var expectedWidth = geometry.RotationDegrees == 0
                ? geometry.PaperWidthMillimetres
                : geometry.PaperHeightMillimetres;
            var expectedHeight = geometry.RotationDegrees == 0
                ? geometry.PaperHeightMillimetres
                : geometry.PaperWidthMillimetres;
            var widthScale = (window.MaxX - window.MinX)
                * geometry.DrawingUnitMillimetres / expectedWidth;
            var heightScale = (window.MaxY - window.MinY)
                * geometry.DrawingUnitMillimetres / expectedHeight;
            if (!PositiveFinite(widthScale) || !PositiveFinite(heightScale))
                return "invalid_plot_scale";

            var average = (widthScale + heightScale) / 2.0;
            var mismatch = Math.Abs(widthScale - heightScale) / average;
            if (mismatch > geometry.ScaleToleranceRatio + 1e-12)
            {
                var alternateWidth = (window.MaxX - window.MinX)
                    * geometry.DrawingUnitMillimetres / expectedHeight;
                var alternateHeight = (window.MaxY - window.MinY)
                    * geometry.DrawingUnitMillimetres / expectedWidth;
                var alternateAverage = (alternateWidth + alternateHeight) / 2.0;
                var alternateMismatch = Math.Abs(alternateWidth - alternateHeight)
                    / alternateAverage;
                return alternateMismatch <= geometry.ScaleToleranceRatio + 1e-12
                    ? "plot_geometry_rotation_mismatch"
                    : "plot_geometry_aspect_mismatch";
            }
            if (Math.Abs(average - geometry.DerivedScaleDenominator)
                > RoundedScaleTolerance)
                return "derived_scale_mismatch";
            if (Math.Abs(geometry.ScaleDenominator - geometry.DerivedScaleDenominator)
                / geometry.ScaleDenominator > geometry.ScaleToleranceRatio + 1e-12)
                return "plot_scale_mismatch";
            return null;
        }

        public static bool IsOneToOneCustomScale(double numerator, double denominator)
        {
            return PositiveFinite(numerator)
                && PositiveFinite(denominator)
                && Math.Abs(numerator - denominator)
                    <= Math.Max(numerator, denominator) * 1e-9;
        }

        private static bool PositiveFinite(double value)
        {
            return value > 0 && Finite(value);
        }

        private static bool Finite(double value)
        {
            return !Double.IsNaN(value) && !Double.IsInfinity(value);
        }
    }
}
