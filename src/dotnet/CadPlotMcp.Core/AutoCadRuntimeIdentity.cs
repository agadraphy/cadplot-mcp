using System;
using System.Text.RegularExpressions;

namespace CadPlotMcp.Core
{
    public static class AutoCadRuntimeIdentity
    {
        private static readonly Regex VersionPrefix = new Regex(
            "^\\s*(?<major>[0-9]{2})\\.(?<minor>[0-9])",
            RegexOptions.CultureInvariant
        );

        public static string NormalizeSeries(string rawAcadVersion)
        {
            if (String.IsNullOrWhiteSpace(rawAcadVersion)) return null;
            var match = VersionPrefix.Match(rawAcadVersion);
            if (!match.Success) return null;
            return "R" + match.Groups["major"].Value + "." + match.Groups["minor"].Value;
        }

        public static bool IsSupported(string adapter, string runtimeSeries)
        {
            if (String.Equals(adapter, "autocad-2016-net45", StringComparison.Ordinal))
                return String.Equals(runtimeSeries, "R20.1", StringComparison.Ordinal);
            if (String.Equals(adapter, "autocad-2025-net8", StringComparison.Ordinal))
                return String.Equals(runtimeSeries, "R25.0", StringComparison.Ordinal);
            return false;
        }
    }
}
