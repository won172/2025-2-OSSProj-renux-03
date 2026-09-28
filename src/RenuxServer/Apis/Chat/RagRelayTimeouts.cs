using Microsoft.Extensions.Configuration;
using System.Diagnostics;
using RenuxServer.Services;

namespace RenuxServer.Apis.Chat;

public sealed record RagRelayTimeoutSettings(
    TimeSpan Connect,
    TimeSpan FirstByte,
    TimeSpan Inactivity,
    TimeSpan Total)
{
    public static RagRelayTimeoutSettings StreamDefaults { get; } = new(
        TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(60),
        TimeSpan.FromSeconds(60), TimeSpan.FromSeconds(180));

    public static RagRelayTimeoutSettings FollowupDefaults { get; } = new(
        TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(30),
        TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(90));

    public static RagRelayTimeoutSettings FromConfiguration(
        IConfiguration configuration, string section, RagRelayTimeoutSettings defaults)
    {
        IConfigurationSection values = configuration.GetSection($"RagRelayTimeouts:{section}");
        return new RagRelayTimeoutSettings(
            Read("ConnectSeconds", defaults.Connect),
            Read("FirstByteSeconds", defaults.FirstByte),
            Read("InactivitySeconds", defaults.Inactivity),
            Read("TotalSeconds", defaults.Total));

        TimeSpan Read(string key, TimeSpan fallback)
        {
            double seconds = values.GetValue<double?>(key) ?? fallback.TotalSeconds;
            if (!double.IsFinite(seconds) || seconds <= 0)
                throw new InvalidOperationException($"RagRelayTimeouts:{section}:{key} must be positive and finite.");
            return TimeSpan.FromSeconds(seconds);
        }
    }
}

public sealed class RagRelayTimeoutException(string phase) : TimeoutException($"RAG relay {phase} timeout.")
{
    public string Phase { get; } = phase;
}

public static class RagRelayCompletion
{
    public static bool CanPersist(bool responseSucceeded, RagTerminalStateMachine terminal,
        bool reportedError, bool cancelled, int answerLength)
        => responseSucceeded && terminal.IsSuccessful && !reportedError && !cancelled && answerLength > 0;
}

/// <summary>Bounds upstream header delivery, first body byte, gaps between body reads, and the whole transfer.</summary>
public sealed class RagRelayTimeouts : IDisposable
{
    private readonly RagRelayTimeoutSettings _settings;
    private readonly CancellationToken _callerToken;
    private readonly CancellationTokenSource _total = new();
    private readonly CancellationTokenSource _upstream;
    private bool _receivedByte;
    private bool _stopped;
    private long _bodyStart;
    private long _lastByteReceived;

    public RagRelayTimeouts(RagRelayTimeoutSettings settings, CancellationToken callerToken)
    {
        _settings = settings;
        _callerToken = callerToken;
        _upstream = CancellationTokenSource.CreateLinkedTokenSource(callerToken, _total.Token);
        _total.CancelAfter(settings.Total);
    }

    public async Task<HttpResponseMessage> SendAsync(HttpClient client, HttpRequestMessage request)
    {
        HttpResponseMessage response = await RunAsync("connect", _settings.Connect,
            token => client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, _upstream.Token)
                .WaitAsync(token));
        _bodyStart = Stopwatch.GetTimestamp();
        return response;
    }

    public async Task<Stream> OpenBodyAsync(HttpResponseMessage response)
    {
        Stream body = await RunAsync("first_byte", FirstByteRemaining(),
            token => response.Content.ReadAsStreamAsync(_upstream.Token).WaitAsync(token));
        return new TimedBodyStream(body, this);
    }

    public async Task<string> ReadBodyToEndAsync(HttpResponseMessage response)
    {
        using var body = await OpenBodyAsync(response);
        using var reader = new StreamReader(body);
        string content = await reader.ReadToEndAsync(_callerToken);
        // Followup persistence and telemetry must never inherit this deadline.
        Stop();
        return content;
    }

    public async Task ForwardAsync(Func<CancellationToken, Task> operation)
    {
        if (_stopped)
        {
            await operation(_callerToken);
            return;
        }
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(_callerToken, _total.Token);
        try
        {
            await operation(linked.Token);
            linked.Token.ThrowIfCancellationRequested();
        }
        catch (OperationCanceledException) when (!_callerToken.IsCancellationRequested && _total.IsCancellationRequested)
        {
            _upstream.Cancel();
            throw new RagRelayTimeoutException("total");
        }
        catch (Exception) when (!_callerToken.IsCancellationRequested && _total.IsCancellationRequested)
        {
            _upstream.Cancel();
            throw new RagRelayTimeoutException("total");
        }
    }

    public void Stop()
    {
        if (_stopped) return;
        _stopped = true;
        _upstream.Dispose();
        _total.Dispose();
    }

    private Task<int> ReadAsync(Stream body, Memory<byte> buffer, CancellationToken token)
        => ReadCoreAsync(body, buffer, token);

    private async Task<int> ReadCoreAsync(Stream body, Memory<byte> buffer, CancellationToken token)
    {
        string phase = _receivedByte ? "inactivity" : "first_byte";
        TimeSpan limit = _receivedByte ? InactivityRemaining() : FirstByteRemaining();
        int count = await RunAsync(phase, limit, linked => body.ReadAsync(buffer, linked).AsTask(), token);
        if (count > 0)
        {
            _receivedByte = true;
            _lastByteReceived = Stopwatch.GetTimestamp();
        }
        return count;
    }

    private TimeSpan InactivityRemaining()
    {
        TimeSpan remaining = _settings.Inactivity - Stopwatch.GetElapsedTime(_lastByteReceived);
        if (remaining > TimeSpan.Zero) return remaining;
        _upstream.Cancel();
        throw new RagRelayTimeoutException(_total.IsCancellationRequested ? "total" : "inactivity");
    }

    private TimeSpan FirstByteRemaining()
    {
        TimeSpan remaining = _settings.FirstByte - Stopwatch.GetElapsedTime(_bodyStart);
        if (remaining > TimeSpan.Zero) return remaining;
        _upstream.Cancel();
        throw new RagRelayTimeoutException(_total.IsCancellationRequested ? "total" : "first_byte");
    }

    private async Task<T> RunAsync<T>(
        string phase, TimeSpan limit, Func<CancellationToken, Task<T>> operation,
        CancellationToken extraToken = default)
    {
        using var phaseTimer = new CancellationTokenSource(limit);
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(
            _upstream.Token, phaseTimer.Token, extraToken);
        try
        {
            T result = await operation(linked.Token);
            linked.Token.ThrowIfCancellationRequested();
            return result;
        }
        catch (OperationCanceledException) when (!_callerToken.IsCancellationRequested && !extraToken.IsCancellationRequested
                                               && (_total.IsCancellationRequested || phaseTimer.IsCancellationRequested))
        {
            _upstream.Cancel();
            throw new RagRelayTimeoutException(_total.IsCancellationRequested ? "total" : phase);
        }
    }

    public void Dispose()
    {
        Stop();
    }

    private sealed class TimedBodyStream(Stream inner, RagRelayTimeouts timeout) : Stream
    {
        public override bool CanRead => inner.CanRead;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override int Read(byte[] buffer, int offset, int count) =>
            ReadAsync(buffer.AsMemory(offset, count)).AsTask().GetAwaiter().GetResult();
        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
            => new(timeout.ReadAsync(inner, buffer, cancellationToken));
        public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
            => timeout.ReadAsync(inner, buffer.AsMemory(offset, count), cancellationToken);
        protected override void Dispose(bool disposing)
        {
            if (disposing) inner.Dispose();
            base.Dispose(disposing);
        }
        public override async ValueTask DisposeAsync()
        {
            await inner.DisposeAsync();
            await base.DisposeAsync();
        }
    }
}
