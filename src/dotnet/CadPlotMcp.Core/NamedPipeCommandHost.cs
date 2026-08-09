using System;
using System.IO;
using System.IO.Pipes;
using System.Text;
#if !NET8_0_OR_GREATER
using System.Security.AccessControl;
using System.Security.Principal;
#endif
using System.Threading;

namespace CadPlotMcp.Core
{
    /// <summary>One-request-per-connection named-pipe host. Requests and responses are UTF-8 JSON lines.</summary>
    public sealed class NamedPipeCommandHost : IDisposable
    {
        private readonly string _pipeName;
        private readonly CommandDispatcher _dispatcher;
        private readonly CancellationTokenSource _stop = new CancellationTokenSource();
        private Thread _thread;

        public NamedPipeCommandHost(string pipeName, CommandDispatcher dispatcher)
        {
            _pipeName = PipeProtocol.ResolvePipeName(pipeName);
            _dispatcher = dispatcher ?? throw new ArgumentNullException("dispatcher");
        }

        public void Start()
        {
            if (_thread != null) return;
            // Reserve the single current-user pipe instance before returning. A second AutoCAD
            // process using the same name then fails visibly during plug-in initialization instead
            // of starting a listener thread that can never become the selected bridge.
            var firstPipe = CreatePipe();
            _thread = new Thread(() => ListenLoop(firstPipe))
            {
                IsBackground = true,
                Name = "CadPlotMcpPipe",
            };
            try { _thread.Start(); }
            catch
            {
                _thread = null;
                firstPipe.Dispose();
                throw;
            }
        }

        private void ListenLoop(NamedPipeServerStream reservedPipe)
        {
            using (var pipe = reservedPipe)
            {
                while (!_stop.IsCancellationRequested)
                {
                    try
                    {
                        pipe.WaitForConnection();
                        using (var reader = new StreamReader(
                            pipe,
                            Encoding.UTF8,
                            true,
                            1024,
                            true
                        ))
                        using (var writer = new StreamWriter(
                            pipe,
                            new UTF8Encoding(false),
                            1024,
                            true
                        ) { AutoFlush = true })
                        {
                            PipeResponse response;
                            try { response = _dispatcher.Dispatch(JsonLineCodec.ReadRequest(JsonLineCodec.ReadLimitedLine(reader))); }
                            catch (Exception) { response = new PipeResponse { Version = PipeProtocol.Version, Error = "invalid_request" }; }
                            writer.WriteLine(JsonLineCodec.WriteResponse(response));
                        }
                    }
                    catch (IOException) { if (_stop.IsCancellationRequested) return; }
                    catch (ObjectDisposedException) { return; }
                    finally
                    {
                        if (pipe.IsConnected)
                        {
                            try { pipe.Disconnect(); }
                            catch (IOException) { }
                            catch (ObjectDisposedException) { }
                        }
                    }
                }
            }
        }

        private NamedPipeServerStream CreatePipe()
        {
#if NET8_0_OR_GREATER
            return new NamedPipeServerStream(
                _pipeName,
                PipeDirection.InOut,
                1,
                PipeTransmissionMode.Byte,
                PipeOptions.CurrentUserOnly
            );
#else
            using (var identity = WindowsIdentity.GetCurrent())
            {
                var user = identity.User;
                if (user == null)
                    throw new InvalidOperationException("Current Windows user SID is unavailable.");
                var security = new PipeSecurity();
                security.SetOwner(user);
                security.SetAccessRuleProtection(true, false);
                security.AddAccessRule(
                    new PipeAccessRule(user, PipeAccessRights.FullControl, AccessControlType.Allow)
                );
                return new NamedPipeServerStream(
                    _pipeName,
                    PipeDirection.InOut,
                    1,
                    PipeTransmissionMode.Byte,
                    PipeOptions.None,
                    0,
                    0,
                    security
                );
            }
#endif
        }

        public void Dispose()
        {
            _stop.Cancel();
            if (_thread != null && _thread.IsAlive)
            {
                try
                {
                    using (var wake = new NamedPipeClientStream(".", _pipeName, PipeDirection.Out))
                        wake.Connect(250);
                }
                catch (IOException) { }
                catch (TimeoutException) { }
                _thread.Join(1000);
            }
            _stop.Dispose();
        }
    }
}
