using System;
using System.Collections.Generic;
using System.IO;
using System.Security;

namespace CadPlotMcp.Core
{
    /// <summary>
    /// Keeps plot output private until every sheet is complete and the staged DWG
    /// has been discarded. Final names are never overwritten.
    /// </summary>
    public sealed class PublishOutputTransaction : IDisposable
    {
        private sealed class OutputEntry
        {
            public string FinalPath { get; set; }
            public string TemporaryPath { get; set; }
        }

        private readonly List<OutputEntry> _entries = new List<OutputEntry>();
        private bool _committed;
        private bool _disposed;

        public PublishOutputTransaction(string outputDirectory, IList<string> finalPaths)
        {
            if (String.IsNullOrWhiteSpace(outputDirectory))
                throw new ArgumentException("Output directory is required.", "outputDirectory");
            if (finalPaths == null || finalPaths.Count < 1 || finalPaths.Count > 5000)
                throw new ArgumentException("Output paths must contain between 1 and 5000 items.", "finalPaths");

            var root = NormalizeDirectory(outputDirectory);
            if (!Directory.Exists(root))
                throw new ArgumentException("Output directory does not exist.", "outputDirectory");
            if ((new DirectoryInfo(root).Attributes & FileAttributes.ReparsePoint) != 0)
                throw new ArgumentException("Output directory must not be redirected.", "outputDirectory");

            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (var index = 0; index < finalPaths.Count; index++)
            {
                var finalPath = Path.GetFullPath(finalPaths[index] ?? String.Empty);
                if (!String.Equals(
                    Path.GetDirectoryName(finalPath),
                    root,
                    StringComparison.OrdinalIgnoreCase
                ) || !String.Equals(Path.GetExtension(finalPath), ".pdf", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("Final PDF must be a direct child of the output directory.", "finalPaths");
                if (!seen.Add(finalPath))
                    throw new ArgumentException("Final PDF paths must be unique.", "finalPaths");
                if (File.Exists(finalPath) || Directory.Exists(finalPath))
                    throw new InvalidOperationException("output_already_exists");

                _entries.Add(new OutputEntry
                {
                    FinalPath = finalPath,
                    TemporaryPath = NewTemporaryPath(root, index + 1),
                });
            }
        }

        public int Count
        {
            get { return _entries.Count; }
        }

        public string GetTemporaryPath(int index)
        {
            if (_disposed) throw new ObjectDisposedException("PublishOutputTransaction");
            if (index < 0 || index >= _entries.Count)
                throw new ArgumentOutOfRangeException("index");
            return _entries[index].TemporaryPath;
        }

        public string Commit()
        {
            if (_disposed) return "output_transaction_disposed";
            if (_committed) return "output_transaction_completed";

            foreach (var entry in _entries)
            {
                if (File.Exists(entry.FinalPath) || Directory.Exists(entry.FinalPath))
                    return "output_already_exists";
                if (!File.Exists(entry.TemporaryPath))
                    return "temporary_output_missing";
                try
                {
                    if (new FileInfo(entry.TemporaryPath).Length < 1)
                        return "temporary_output_empty";
                }
                catch (UnauthorizedAccessException)
                {
                    return "temporary_output_access_denied";
                }
                catch (SecurityException)
                {
                    return "temporary_output_access_denied";
                }
                catch (IOException)
                {
                    return "temporary_output_io_error";
                }
            }

            var promoted = 0;
            try
            {
                foreach (var entry in _entries)
                {
                    File.Move(entry.TemporaryPath, entry.FinalPath);
                    promoted++;
                }
                _committed = true;
                return null;
            }
            catch (UnauthorizedAccessException)
            {
                return promoted == 0 ? "output_commit_access_denied" : "output_commit_partial";
            }
            catch (SecurityException)
            {
                return promoted == 0 ? "output_commit_access_denied" : "output_commit_partial";
            }
            catch (IOException)
            {
                return promoted == 0 ? "output_commit_io_error" : "output_commit_partial";
            }
        }

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            if (_committed) return;
            foreach (var entry in _entries)
            {
                if (!File.Exists(entry.TemporaryPath)) continue;
                try { File.Delete(entry.TemporaryPath); }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                catch (SecurityException) { }
            }
        }

        private static string NewTemporaryPath(string root, int index)
        {
            for (var attempt = 0; attempt < 10; attempt++)
            {
                var candidate = Path.Combine(
                    root,
                    ".cadplot-" + index.ToString("0000") + "-"
                    + Guid.NewGuid().ToString("N") + ".partial.pdf"
                );
                if (!File.Exists(candidate) && !Directory.Exists(candidate)) return candidate;
            }
            throw new IOException("Could not reserve a unique temporary output name.");
        }

        private static string NormalizeDirectory(string value)
        {
            var full = Path.GetFullPath(value);
            var root = Path.GetPathRoot(full);
            return full.Length > root.Length
                ? full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                : full;
        }
    }
}
