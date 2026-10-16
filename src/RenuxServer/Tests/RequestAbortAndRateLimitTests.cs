using System.Text;
using System.Text.Json;
using System.Threading.RateLimiting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using RenuxServer.Middlewares;

internal static class RequestAbortAndRateLimitTests
{
    public static async Task RunAsync(Action<bool, string> check)
    {
        await CheckAbortedRequestAsync(check);
        await CheckRateLimitRejectionAsync(check);
    }

    private static async Task CheckAbortedRequestAsync(Action<bool, string> check)
    {
        // 클라이언트 연결 끊김: Error 로그·본문 없이 끝나야 한다.
        using (var cts = new CancellationTokenSource())
        {
            cts.Cancel();
            var logger = new RecordingLogger<GlobalExceptionHandlerMiddleware>();
            var context = NewContext();
            context.RequestAborted = cts.Token;
            var middleware = new GlobalExceptionHandlerMiddleware(
                _ => throw new OperationCanceledException(cts.Token), logger, new TestHostEnvironment());

            var threw = false;
            try
            {
                await middleware.InvokeAsync(context);
            }
            catch
            {
                threw = true;
            }

            check(!threw, "An aborted request must not rethrow from the global exception middleware.");
            check(!logger.Entries.Any(entry => entry.Level >= LogLevel.Warning),
                "An aborted request must not be logged at Warning/Error.");
            check(context.Response.StatusCode == StatusCodes.Status200OK && context.Response.Body.Length == 0,
                "An aborted request must not get a 500 status or a response body.");
        }

        // 요청이 끊기지 않았는데 발생한 OperationCanceledException(예: 내부 타임아웃)은 기존처럼 500이다.
        {
            var logger = new RecordingLogger<GlobalExceptionHandlerMiddleware>();
            var context = NewContext();
            var middleware = new GlobalExceptionHandlerMiddleware(
                _ => throw new OperationCanceledException(), logger, new TestHostEnvironment());
            await middleware.InvokeAsync(context);
            check(context.Response.StatusCode == StatusCodes.Status500InternalServerError
                  && logger.Entries.Any(entry => entry.Level == LogLevel.Error && entry.Message.Contains("Unhandled exception", StringComparison.Ordinal)),
                "A non-aborted OperationCanceledException must still be logged as Error and return 500.");
            // 기존 본문은 기본 JsonSerializer라 한글이 \uXXXX로 이스케이프되므로 파싱해서 비교한다.
            check(JsonDocument.Parse(ReadBody(context)).RootElement.TryGetProperty("Message", out var serverMessage)
                  && serverMessage.GetString() == "서버 오류 관리자 문의",
                "A non-aborted OperationCanceledException must keep the existing 500 JSON body.");
        }

        // 그 밖의 예외도 그대로 500이다.
        {
            var logger = new RecordingLogger<GlobalExceptionHandlerMiddleware>();
            var context = NewContext();
            var middleware = new GlobalExceptionHandlerMiddleware(
                _ => throw new InvalidOperationException("boom"), logger, new TestHostEnvironment());
            await middleware.InvokeAsync(context);
            check(context.Response.StatusCode == StatusCodes.Status500InternalServerError
                  && logger.Entries.Any(entry => entry.Level == LogLevel.Error),
                "Other exceptions must still be logged as Error and return 500.");
        }
    }

    private static async Task CheckRateLimitRejectionAsync(Action<bool, string> check)
    {
        using var limiter = new FixedWindowRateLimiter(new FixedWindowRateLimiterOptions
        {
            PermitLimit = 1,
            Window = TimeSpan.FromMinutes(1),
            QueueLimit = 0,
            AutoReplenishment = false,
        });
        using var first = limiter.AttemptAcquire();
        using var rejected = limiter.AttemptAcquire();
        check(first.IsAcquired && !rejected.IsAcquired, "Test setup: the second fixed-window acquire must be rejected.");

        var context = NewContext();
        context.Response.StatusCode = StatusCodes.Status429TooManyRequests;
        await RateLimitRejectionHandler.OnRejectedAsync(
            new OnRejectedContext { HttpContext = context, Lease = rejected }, CancellationToken.None);

        check(context.Response.StatusCode == StatusCodes.Status429TooManyRequests, "A rate-limit rejection must stay 429.");
        check(context.Response.ContentType?.StartsWith("application/json", StringComparison.Ordinal) == true,
            "A rate-limit rejection must return JSON.");
        var body = JsonDocument.Parse(ReadBody(context)).RootElement;
        check(body.TryGetProperty("error", out var error) && error.GetString() == "rate_limited"
              && body.TryGetProperty("message", out var message) && !string.IsNullOrWhiteSpace(message.GetString()),
            "A rate-limit rejection must use the {error, message} error shape with error=rate_limited.");
        check(int.TryParse(context.Response.Headers.RetryAfter.ToString(), out var seconds) && seconds >= 1 && seconds <= 60,
            $"A fixed-window rejection must include Retry-After in seconds (got '{context.Response.Headers.RetryAfter}').");

        var noMetadata = NewContext();
        noMetadata.Response.StatusCode = StatusCodes.Status429TooManyRequests;
        await RateLimitRejectionHandler.OnRejectedAsync(
            new OnRejectedContext { HttpContext = noMetadata, Lease = new BareFailedLease() }, CancellationToken.None);
        check(noMetadata.Response.StatusCode == StatusCodes.Status429TooManyRequests
              && !noMetadata.Response.Headers.ContainsKey("Retry-After")
              && ReadBody(noMetadata).Contains("rate_limited", StringComparison.Ordinal),
            "Without RetryAfter metadata the rejection must still be a 429 JSON body and omit Retry-After.");
    }

    private static DefaultHttpContext NewContext()
    {
        var context = new DefaultHttpContext();
        context.Response.Body = new MemoryStream();
        return context;
    }

    private static string ReadBody(HttpContext context)
    {
        context.Response.Body.Position = 0;
        return new StreamReader(context.Response.Body, Encoding.UTF8).ReadToEnd();
    }

    private sealed class BareFailedLease : RateLimitLease
    {
        public override bool IsAcquired => false;
        public override IEnumerable<string> MetadataNames => [];
        public override bool TryGetMetadata(string metadataName, out object? metadata)
        {
            metadata = null;
            return false;
        }
    }

    private sealed class TestHostEnvironment : IHostEnvironment
    {
        public string EnvironmentName { get; set; } = Environments.Production;
        public string ApplicationName { get; set; } = "RenuxServer.Tests";
        public string ContentRootPath { get; set; } = AppContext.BaseDirectory;
        public IFileProvider ContentRootFileProvider { get; set; } = new NullFileProvider();
    }

    private sealed class RecordingLogger<T> : ILogger<T>
    {
        public List<(LogLevel Level, string Message)> Entries { get; } = [];
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(LogLevel logLevel) => true;
        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
            => Entries.Add((logLevel, formatter(state, exception)));
    }
}
