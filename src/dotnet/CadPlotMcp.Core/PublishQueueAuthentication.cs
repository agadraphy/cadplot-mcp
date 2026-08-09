using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

namespace CadPlotMcp.Core
{
    internal static class PublishQueueKeyStore
    {
        private const string FileName = "queue-auth-key-v1.bin";
        private static readonly byte[] Header = Encoding.ASCII.GetBytes("CADPLOT_QUEUE_KEY_V1\n");
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes(
            "CadPlotMcp.QueueJournal.Authentication.v1"
        );

        public static string DefaultPath()
        {
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (String.IsNullOrWhiteSpace(local))
                throw new InvalidOperationException("local_application_data_unavailable");
            return Path.Combine(local, "CadPlotMcp", "state", FileName);
        }

        public static byte[] LoadOrCreate(string keyPath, string trustedWorkspaceRoot)
        {
            if (String.IsNullOrWhiteSpace(keyPath))
                throw new ArgumentException("A queue authentication key path is required.", "keyPath");
            var fullPath = Path.GetFullPath(keyPath ?? String.Empty);
            var workspace = Path.GetFullPath(trustedWorkspaceRoot ?? String.Empty);
            if (IsInside(fullPath, workspace))
                throw new ArgumentException(
                    "The queue authentication key must be outside the trusted workspace.",
                    "keyPath"
                );
            var directory = Path.GetDirectoryName(fullPath);
            if (String.IsNullOrWhiteSpace(directory))
                throw new ArgumentException("The queue authentication key path is invalid.", "keyPath");
            Directory.CreateDirectory(directory);
            if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("queue_auth_key_directory_redirected");
            if (File.Exists(fullPath)) return Read(fullPath);

            var raw = new byte[32];
            using (var random = RandomNumberGenerator.Create()) random.GetBytes(raw);
            try
            {
                var protectedBytes = WindowsDataProtection.Protect(raw, Entropy);
                var payload = new byte[Header.Length + protectedBytes.Length];
                Buffer.BlockCopy(Header, 0, payload, 0, Header.Length);
                Buffer.BlockCopy(protectedBytes, 0, payload, Header.Length, protectedBytes.Length);
                try
                {
                    WriteAtomicCreate(fullPath, payload);
                }
                catch (IOException)
                {
                    if (!File.Exists(fullPath)) throw;
                }
                finally
                {
                    Array.Clear(payload, 0, payload.Length);
                    Array.Clear(protectedBytes, 0, protectedBytes.Length);
                }
            }
            finally
            {
                Array.Clear(raw, 0, raw.Length);
            }
            return Read(fullPath);
        }

        private static byte[] Read(string path)
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidDataException("queue_auth_key_redirected");
            byte[] payload;
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (stream.Length <= Header.Length || stream.Length > 8192)
                    throw new InvalidDataException("queue_auth_key_size_invalid");
                payload = new byte[stream.Length];
                var offset = 0;
                while (offset < payload.Length)
                {
                    var read = stream.Read(payload, offset, payload.Length - offset);
                    if (read == 0) throw new EndOfStreamException("queue_auth_key_truncated");
                    offset += read;
                }
            }
            try
            {
                for (var index = 0; index < Header.Length; index++)
                    if (payload[index] != Header[index])
                        throw new InvalidDataException("queue_auth_key_header_invalid");
                var protectedBytes = new byte[payload.Length - Header.Length];
                Buffer.BlockCopy(payload, Header.Length, protectedBytes, 0, protectedBytes.Length);
                try
                {
                    var raw = WindowsDataProtection.Unprotect(protectedBytes, Entropy);
                    if (raw == null || raw.Length != 32)
                    {
                        if (raw != null) Array.Clear(raw, 0, raw.Length);
                        throw new InvalidDataException("queue_auth_key_material_invalid");
                    }
                    return raw;
                }
                finally
                {
                    Array.Clear(protectedBytes, 0, protectedBytes.Length);
                }
            }
            finally
            {
                Array.Clear(payload, 0, payload.Length);
            }
        }

        private static void WriteAtomicCreate(string finalPath, byte[] payload)
        {
            var temporaryPath = Path.Combine(
                Path.GetDirectoryName(finalPath),
                ".cadplot-key-" + Guid.NewGuid().ToString("N") + ".tmp"
            );
            try
            {
                using (var stream = new FileStream(
                    temporaryPath,
                    FileMode.CreateNew,
                    FileAccess.Write,
                    FileShare.None
                ))
                {
                    stream.Write(payload, 0, payload.Length);
                    stream.Flush(true);
                }
                File.Move(temporaryPath, finalPath);
                temporaryPath = null;
            }
            finally
            {
                if (temporaryPath != null && File.Exists(temporaryPath))
                {
                    try { File.Delete(temporaryPath); }
                    catch { }
                }
            }
        }

        private static bool IsInside(string candidate, string root)
        {
            var normalizedCandidate = Path.GetFullPath(candidate);
            var normalizedRoot = Path.GetFullPath(root).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar
            ) + Path.DirectorySeparatorChar;
            return normalizedCandidate.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase);
        }
    }

    internal sealed class PublishQueueAuthenticator
    {
        private readonly byte[] _key;

        public PublishQueueAuthenticator(byte[] key)
        {
            if (key == null || key.Length != 32)
                throw new ArgumentException("A 256-bit queue authentication key is required.", "key");
            _key = (byte[])key.Clone();
        }

        public string Sign(PublishQueueRequestRecord record)
        {
            return Sign(writer =>
            {
                WriteField(writer, "cadplot-queue-request-v1");
                writer.Write(record.SchemaVersion);
                WriteField(writer, record.PlanId);
                WriteField(writer, record.ManifestPath);
                WriteField(writer, record.ManifestSha256);
                WriteField(writer, record.StagedDrawing);
                WriteField(writer, record.OutputDirectory);
                writer.Write(record.SheetCount);
                WriteField(writer, record.QueuedUtc);
            });
        }

        public string Sign(PublishQueueStartedRecord record)
        {
            return Sign(writer =>
            {
                WriteField(writer, "cadplot-queue-started-v1");
                writer.Write(record.SchemaVersion);
                WriteField(writer, record.PlanId);
                WriteField(writer, record.ManifestSha256);
                WriteField(writer, record.StartedUtc);
            });
        }

        public bool Verify(PublishQueueRequestRecord record)
        {
            return record != null
                && record.AuthenticationVersion == 1
                && FixedTimeEquals(record.AuthenticationTag, Sign(record));
        }

        public bool Verify(PublishQueueStartedRecord record)
        {
            return record != null
                && record.AuthenticationVersion == 1
                && FixedTimeEquals(record.AuthenticationTag, Sign(record));
        }

        private string Sign(Action<BinaryWriter> write)
        {
            byte[] payload;
            using (var stream = new MemoryStream())
            {
                using (var writer = new BinaryWriter(stream, new UTF8Encoding(false), true)) write(writer);
                payload = stream.ToArray();
            }
            try
            {
                using (var hmac = new HMACSHA256(_key))
                    return Convert.ToBase64String(hmac.ComputeHash(payload));
            }
            finally
            {
                Array.Clear(payload, 0, payload.Length);
            }
        }

        private static void WriteField(BinaryWriter writer, string value)
        {
            if (value == null)
            {
                writer.Write(-1);
                return;
            }
            var bytes = Encoding.UTF8.GetBytes(value);
            writer.Write(bytes.Length);
            writer.Write(bytes);
        }

        private static bool FixedTimeEquals(string left, string right)
        {
            byte[] leftBytes;
            byte[] rightBytes;
            try
            {
                leftBytes = Convert.FromBase64String(left ?? String.Empty);
                rightBytes = Convert.FromBase64String(right ?? String.Empty);
            }
            catch (FormatException)
            {
                return false;
            }
            try
            {
                var difference = leftBytes.Length ^ rightBytes.Length;
                var length = Math.Min(leftBytes.Length, rightBytes.Length);
                for (var index = 0; index < length; index++)
                    difference |= leftBytes[index] ^ rightBytes[index];
                return difference == 0;
            }
            finally
            {
                Array.Clear(leftBytes, 0, leftBytes.Length);
                Array.Clear(rightBytes, 0, rightBytes.Length);
            }
        }
    }

    internal static class WindowsDataProtection
    {
        private const int CryptProtectUiForbidden = 0x1;

        [StructLayout(LayoutKind.Sequential)]
        private struct DataBlob
        {
            public int Size;
            public IntPtr Data;
        }

        [DllImport("Crypt32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CryptProtectData(
            ref DataBlob dataIn,
            string description,
            ref DataBlob optionalEntropy,
            IntPtr reserved,
            IntPtr prompt,
            int flags,
            out DataBlob dataOut
        );

        [DllImport("Crypt32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CryptUnprotectData(
            ref DataBlob dataIn,
            IntPtr description,
            ref DataBlob optionalEntropy,
            IntPtr reserved,
            IntPtr prompt,
            int flags,
            out DataBlob dataOut
        );

        [DllImport("Kernel32.dll", SetLastError = true)]
        private static extern IntPtr LocalFree(IntPtr memory);

        public static byte[] Protect(byte[] value, byte[] entropy)
        {
            return Transform(value, entropy, true);
        }

        public static byte[] Unprotect(byte[] value, byte[] entropy)
        {
            return Transform(value, entropy, false);
        }

        private static byte[] Transform(byte[] value, byte[] entropy, bool protect)
        {
            if (value == null || value.Length == 0)
                throw new ArgumentException("DPAPI input is required.", "value");
            var input = CreateBlob(value);
            var optionalEntropy = CreateBlob(entropy ?? new byte[0]);
            var output = new DataBlob();
            try
            {
                var succeeded = protect
                    ? CryptProtectData(
                        ref input,
                        "CadPlot MCP queue authentication key",
                        ref optionalEntropy,
                        IntPtr.Zero,
                        IntPtr.Zero,
                        CryptProtectUiForbidden,
                        out output
                    )
                    : CryptUnprotectData(
                        ref input,
                        IntPtr.Zero,
                        ref optionalEntropy,
                        IntPtr.Zero,
                        IntPtr.Zero,
                        CryptProtectUiForbidden,
                        out output
                    );
                if (!succeeded)
                    throw new CryptographicException(
                        "Windows DPAPI operation failed.",
                        new Win32Exception(Marshal.GetLastWin32Error())
                    );
                if (output.Size <= 0 || output.Data == IntPtr.Zero)
                    throw new CryptographicException("Windows DPAPI returned no data.");
                var result = new byte[output.Size];
                Marshal.Copy(output.Data, result, 0, result.Length);
                return result;
            }
            finally
            {
                FreeBlob(ref input, false);
                FreeBlob(ref optionalEntropy, false);
                FreeBlob(ref output, true);
            }
        }

        private static DataBlob CreateBlob(byte[] value)
        {
            if (value == null || value.Length == 0) return new DataBlob();
            var blob = new DataBlob
            {
                Size = value.Length,
                Data = Marshal.AllocHGlobal(value.Length),
            };
            Marshal.Copy(value, 0, blob.Data, value.Length);
            return blob;
        }

        private static void FreeBlob(ref DataBlob blob, bool local)
        {
            if (blob.Data == IntPtr.Zero) return;
            if (local) LocalFree(blob.Data);
            else Marshal.FreeHGlobal(blob.Data);
            blob.Data = IntPtr.Zero;
            blob.Size = 0;
        }
    }
}
