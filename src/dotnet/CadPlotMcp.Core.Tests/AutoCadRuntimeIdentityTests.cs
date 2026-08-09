using CadPlotMcp.Core;
using Xunit;

namespace CadPlotMcp.Core.Tests;

public sealed class AutoCadRuntimeIdentityTests
{
    [Theory]
    [InlineData("20.1s (LMS Tech)", "R20.1")]
    [InlineData("25.0s (LMS Tech)", "R25.0")]
    [InlineData(" 25.1s (LMS Tech)", "R25.1")]
    [InlineData("not-a-version", null)]
    [InlineData(null, null)]
    public void OfficialAcadverValuesNormalizeToManagedSeries(string? raw, string? expected)
    {
        Assert.Equal(expected, AutoCadRuntimeIdentity.NormalizeSeries(raw!));
    }

    [Theory]
    [InlineData("autocad-2016-net45", "R20.1", true)]
    [InlineData("autocad-2016-net45", "R20.0", false)]
    [InlineData("autocad-2016-net45", "R21.0", false)]
    [InlineData("autocad-2025-net8", "R25.0", true)]
    [InlineData("autocad-2025-net8", "R25.1", true)]
    [InlineData("autocad-2025-net8", "R24.3", false)]
    [InlineData("unknown", "R25.0", false)]
    public void AdapterAllowsOnlyItsDeclaredRuntimeSeries(
        string adapter,
        string series,
        bool expected
    )
    {
        Assert.Equal(expected, AutoCadRuntimeIdentity.IsSupported(adapter, series));
    }
}
