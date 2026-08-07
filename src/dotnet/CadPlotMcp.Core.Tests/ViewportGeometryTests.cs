using CadPlotMcp.Core;
using Xunit;

namespace CadPlotMcp.Core.Tests;

public sealed class ViewportGeometryTests
{
    [Fact]
    public void MillimetrePaperPreservesOneToFiftyScale()
    {
        var result = ViewportGeometryCalculator.Calculate(
            Geometry(0, 0, 35000, 50000, denominator: 50),
            700,
            1000,
            paperUnitMillimetres: 1
        );

        Assert.Equal(700, result.PaperWidthUnits);
        Assert.Equal(1000, result.PaperHeightUnits);
        Assert.Equal(17500, result.ModelCenterX);
        Assert.Equal(25000, result.ModelCenterY);
        Assert.Equal(0.02, result.CustomScale, 10);
        Assert.False(result.QuarterTurn);
    }

    [Fact]
    public void LandscapeModelWindowRequestsQuarterTurnOnPortraitPaper()
    {
        var result = ViewportGeometryCalculator.Calculate(
            Geometry(10, 20, 307, 230, denominator: 1),
            210,
            297,
            paperUnitMillimetres: 1
        );

        Assert.True(result.QuarterTurn);
        Assert.Equal(158.5, result.ModelCenterX);
        Assert.Equal(125, result.ModelCenterY);
        Assert.Equal(1, result.CustomScale);
    }

    [Fact]
    public void InchPaperConvertsViewportAndScaleUnits()
    {
        var result = ViewportGeometryCalculator.Calculate(
            Geometry(0, 0, 215.9, 279.4, denominator: 1),
            215.9,
            279.4,
            paperUnitMillimetres: 25.4
        );

        Assert.Equal(8.5, result.PaperWidthUnits, 10);
        Assert.Equal(11, result.PaperHeightUnits, 10);
        Assert.Equal(1.0 / 25.4, result.CustomScale, 10);
        Assert.False(result.QuarterTurn);
    }

    private static PublishPlotGeometry Geometry(
        double minX,
        double minY,
        double maxX,
        double maxY,
        double denominator
    ) => new()
    {
        Window = new PublishPlotWindow { MinX = minX, MinY = minY, MaxX = maxX, MaxY = maxY },
        DrawingUnitMillimetres = 1,
        ScaleDenominator = denominator,
        PaperWidthMillimetres = 1,
        PaperHeightMillimetres = 1,
    };
}
