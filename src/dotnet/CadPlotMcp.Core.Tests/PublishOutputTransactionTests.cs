using CadPlotMcp.Core;
using Xunit;

namespace CadPlotMcp.Core.Tests;

public sealed class PublishOutputTransactionTests : IDisposable
{
    private readonly string _root;
    private readonly string[] _finals;

    public PublishOutputTransactionTests()
    {
        _root = Path.Combine(
            Path.GetTempPath(),
            "cadplot-output-transaction-" + Guid.NewGuid().ToString("N")
        );
        Directory.CreateDirectory(_root);
        _finals =
        [
            Path.Combine(_root, "0001-first.pdf"),
            Path.Combine(_root, "0002-second.pdf"),
        ];
    }

    [Fact]
    public void CompleteJobPromotesEveryTemporaryPdfWithoutOverwrite()
    {
        using var transaction = new PublishOutputTransaction(_root, _finals);
        File.WriteAllText(transaction.GetTemporaryPath(0), "first PDF");
        File.WriteAllText(transaction.GetTemporaryPath(1), "second PDF");

        var error = transaction.Commit();

        Assert.Null(error);
        Assert.Equal("first PDF", File.ReadAllText(_finals[0]));
        Assert.Equal("second PDF", File.ReadAllText(_finals[1]));
        Assert.DoesNotContain(
            Directory.GetFiles(_root),
            path => path.EndsWith(".partial.pdf", StringComparison.OrdinalIgnoreCase)
        );
    }

    [Fact]
    public void PlotFailureDisposesAllTemporaryPdfsAndPublishesNothing()
    {
        string firstTemporary;
        string secondTemporary;
        using (var transaction = new PublishOutputTransaction(_root, _finals))
        {
            firstTemporary = transaction.GetTemporaryPath(0);
            secondTemporary = transaction.GetTemporaryPath(1);
            File.WriteAllText(firstTemporary, "completed first plot");
            File.WriteAllText(secondTemporary, "partial second plot");
        }

        Assert.False(File.Exists(firstTemporary));
        Assert.False(File.Exists(secondTemporary));
        Assert.All(_finals, path => Assert.False(File.Exists(path)));
    }

    [Fact]
    public void CommitRefusesMissingOrEmptyTemporaryOutput()
    {
        using var missing = new PublishOutputTransaction(_root, _finals);
        File.WriteAllText(missing.GetTemporaryPath(0), "first PDF");
        Assert.Equal("temporary_output_missing", missing.Commit());
        Assert.All(_finals, path => Assert.False(File.Exists(path)));

        using var empty = new PublishOutputTransaction(_root, _finals);
        File.WriteAllText(empty.GetTemporaryPath(0), "first PDF");
        File.WriteAllBytes(empty.GetTemporaryPath(1), []);
        Assert.Equal("temporary_output_empty", empty.Commit());
        Assert.All(_finals, path => Assert.False(File.Exists(path)));
    }

    [Fact]
    public void FinalOutputRaceIsNeverOverwritten()
    {
        using var transaction = new PublishOutputTransaction(_root, _finals);
        File.WriteAllText(transaction.GetTemporaryPath(0), "new first");
        File.WriteAllText(transaction.GetTemporaryPath(1), "new second");
        File.WriteAllText(_finals[1], "external output");

        Assert.Equal("output_already_exists", transaction.Commit());
        Assert.False(File.Exists(_finals[0]));
        Assert.Equal("external output", File.ReadAllText(_finals[1]));
    }

    [Fact]
    public void ConstructorRefusesExistingDuplicateOrEscapedFinals()
    {
        File.WriteAllText(_finals[0], "existing");
        var existing = Assert.Throws<InvalidOperationException>(
            () => new PublishOutputTransaction(_root, _finals)
        );
        Assert.Equal("output_already_exists", existing.Message);
        File.Delete(_finals[0]);

        Assert.Throws<ArgumentException>(
            () => new PublishOutputTransaction(_root, [_finals[0], _finals[0]])
        );
        Assert.Throws<ArgumentException>(
            () => new PublishOutputTransaction(
                _root,
                [Path.Combine(Path.GetDirectoryName(_root)!, "outside.pdf")]
            )
        );
    }

    [Fact]
    public void TransactionCannotBeCommittedTwiceOrUsedAfterDispose()
    {
        var transaction = new PublishOutputTransaction(_root, [_finals[0]]);
        File.WriteAllText(transaction.GetTemporaryPath(0), "PDF");
        Assert.Null(transaction.Commit());
        Assert.Equal("output_transaction_completed", transaction.Commit());
        transaction.Dispose();
        Assert.Equal("output_transaction_disposed", transaction.Commit());
        Assert.Throws<ObjectDisposedException>(() => transaction.GetTemporaryPath(0));
    }

    public void Dispose()
    {
        if (Directory.Exists(_root)) Directory.Delete(_root, recursive: true);
    }
}
