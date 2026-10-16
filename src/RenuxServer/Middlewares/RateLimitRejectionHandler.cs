using System.Globalization;
using System.Threading.RateLimiting;
using Microsoft.AspNetCore.RateLimiting;
using RenuxServer.Apis;

namespace RenuxServer.Middlewares;

/// <summary>
/// 레이트 리밋 거부(429) 응답. 모든 정책에 공통으로 적용되며, 상태 코드는 RejectionStatusCode(429)를
/// 그대로 쓰고 다른 API 오류와 같은 {error, message} JSON 본문과 가능하면 Retry-After를 붙인다.
/// </summary>
public static class RateLimitRejectionHandler
{
    internal const string ErrorCode = "rate_limited";
    internal const string Message = "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.";

    public static async ValueTask OnRejectedAsync(OnRejectedContext context, CancellationToken cancellationToken)
    {
        var response = context.HttpContext.Response;
        if (response.HasStarted)
        {
            return;
        }

        response.StatusCode = StatusCodes.Status429TooManyRequests;
        if (context.Lease.TryGetMetadata(MetadataName.RetryAfter, out TimeSpan retryAfter))
        {
            var seconds = Math.Max(1, (int)Math.Ceiling(retryAfter.TotalSeconds));
            response.Headers.RetryAfter = seconds.ToString(CultureInfo.InvariantCulture);
        }

        await response.WriteAsJsonAsync(new CalendarErrorResponse(ErrorCode, Message), cancellationToken);
    }
}
