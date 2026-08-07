using System;
using System.Threading;

namespace CadPlotMcp.Core
{
    public interface IPublishJobExecutor
    {
        PublishExecutionResult Execute(PublishJobRequest request);
    }

    public sealed class PublishExecutionResult
    {
        public bool Succeeded { get; private set; }
        public string Error { get; private set; }

        public static PublishExecutionResult Success()
        {
            return new PublishExecutionResult { Succeeded = true };
        }

        public static PublishExecutionResult Failure(string error)
        {
            return new PublishExecutionResult
            {
                Succeeded = false,
                Error = String.IsNullOrWhiteSpace(error) ? "publish_failed" : error,
            };
        }
    }

    public sealed class PublishProcessResult
    {
        public bool Processed { get; set; }
        public bool Succeeded { get; set; }
        public string PlanId { get; set; }
        public string Error { get; set; }
    }

    public sealed class PublishJobWorker
    {
        private readonly PublishJobQueue _queue;
        private readonly IPublishJobExecutor _executor;
        private int _busy;

        public PublishJobWorker(PublishJobQueue queue, IPublishJobExecutor executor)
        {
            _queue = queue ?? throw new ArgumentNullException("queue");
            _executor = executor ?? throw new ArgumentNullException("executor");
        }

        public PublishProcessResult ProcessNext()
        {
            if (Interlocked.CompareExchange(ref _busy, 1, 0) != 0)
                return new PublishProcessResult { Error = "worker_busy" };
            try
            {
                PublishJobRequest request;
                if (!_queue.TryStartNext(out request))
                    return new PublishProcessResult { Error = "no_pending_job" };

                PublishExecutionResult execution;
                try
                {
                    execution = _executor.Execute(request)
                        ?? PublishExecutionResult.Failure("publisher_returned_no_result");
                }
                catch (Exception exception)
                {
                    execution = PublishExecutionResult.Failure(
                        "publisher_exception:" + exception.GetType().Name
                    );
                }
                _queue.Complete(request.PlanId, execution.Succeeded, execution.Error);
                return new PublishProcessResult
                {
                    Processed = true,
                    Succeeded = execution.Succeeded,
                    PlanId = request.PlanId,
                    Error = execution.Error,
                };
            }
            finally
            {
                Volatile.Write(ref _busy, 0);
            }
        }
    }
}
