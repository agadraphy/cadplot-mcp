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

    [Fact]
    public void GeometryContractAcceptsApprovedRotatedScaleMath()
    {
        var geometry = Geometry(0, 0, 297, 210, denominator: 1);
        geometry.RotationDegrees = 90;
        geometry.PaperWidthMillimetres = 210;
        geometry.PaperHeightMillimetres = 297;
        geometry.DerivedScaleDenominator = 1;
        geometry.ScaleToleranceRatio = 0.02;

        Assert.Null(PublishGeometryContract.Validate(geometry));
    }

    [Fact]
    public void GeometryContractRejectsRotationThatContradictsWindowAndPaper()
    {
        var geometry = Geometry(0, 0, 297, 210, denominator: 1);
        geometry.RotationDegrees = 0;
        geometry.PaperWidthMillimetres = 210;
        geometry.PaperHeightMillimetres = 297;
        geometry.DerivedScaleDenominator = 1;
        geometry.ScaleToleranceRatio = 0.02;

        Assert.Equal(
            "plot_geometry_rotation_mismatch",
            PublishGeometryContract.Validate(geometry)
        );
    }

    [Fact]
    public void GeometryContractRejectsChangedSelectedScale()
    {
        var geometry = Geometry(0, 0, 35000, 50000, denominator: 100);
        geometry.RotationDegrees = 0;
        geometry.PaperWidthMillimetres = 700;
        geometry.PaperHeightMillimetres = 1000;
        geometry.DerivedScaleDenominator = 50;
        geometry.ScaleToleranceRatio = 0.02;

        Assert.Equal("plot_scale_mismatch", PublishGeometryContract.Validate(geometry));
    }

    [Theory]
    [InlineData(1.0, 1.0, true)]
    [InlineData(25.4, 25.4, true)]
    [InlineData(1.0, 2.0, false)]
    [InlineData(0.0, 0.0, false)]
    public void CustomLayoutPlotScaleMustBeOneToOne(
        double numerator,
        double denominator,
        bool expected
    )
    {
        Assert.Equal(
            expected,
            PublishGeometryContract.IsOneToOneCustomScale(numerator, denominator)
        );
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
        DerivedScaleDenominator = denominator,
        ScaleToleranceRatio = 0.02,
        PaperWidthMillimetres = 1,
        PaperHeightMillimetres = 1,
    };
}
