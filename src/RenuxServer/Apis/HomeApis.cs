using Microsoft.Extensions.Caching.Memory;
using System.Security.Claims;

namespace RenuxServer.Apis;

static public class HomeApis
{
    /// <summary>
    /// 브리핑 캐시 수명. 학식·학사일정·공지는 분 단위로 바뀌지 않으므로
    /// 방문마다 RAG를 호출하지 않고 짧게 캐시한다.
    /// </summary>
    private static readonly TimeSpan BriefingCacheDuration = TimeSpan.FromMinutes(5);

    private const string BriefingCacheKey = "home-briefing";

    /// <summary>브리핑이 없어도 홈은 떠야 하므로, 실패 시 빈 구조를 돌려준다.</summary>
    private static readonly object EmptyBriefing = new
    {
        generatedAt = (string?)null,
        meals = Array.Empty<object>(),
        schedules = Array.Empty<object>(),
        notices = Array.Empty<object>(),
    };

    static public void AddHomeApis(this WebApplication application)
    {
        var app = application.MapGroup("/home");
        string ragServiceUrl = application.Configuration["RagServiceUrl"]
            ?? application.Configuration["RAG_SERVICE_URL"]
            ?? "http://rag-service:8000";

        // 홈 '오늘' 브리핑. 게스트는 전체 공개 공지만, 로그인 학생은 본인 학과 범위까지 본다.
        app.MapGet("/briefing", async (
            HttpContext context,
            IHttpClientFactory httpClientFactory,
            IMemoryCache cache,
            ILogger<Program> logger) =>
        {
            string? major = context.User.Identity?.IsAuthenticated == true
                ? context.User.FindFirstValue("Major")
                : null;
            if (string.IsNullOrWhiteSpace(major) || major == "Unknown")
            {
                major = null;
            }

            string cacheKey = major is null
                ? BriefingCacheKey
                : $"{BriefingCacheKey}:major:{major}";
            if (cache.TryGetValue(cacheKey, out string? cached) && cached is not null)
            {
                return Results.Content(cached, "application/json");
            }

            try
            {
                var client = httpClientFactory.CreateClient();
                client.Timeout = TimeSpan.FromSeconds(8);
                string briefingUrl = major is null
                    ? $"{ragServiceUrl}/home/briefing"
                    : $"{ragServiceUrl}/home/briefing?major={Uri.EscapeDataString(major)}";
                var response = await client.GetAsync(briefingUrl, context.RequestAborted);

                if (!response.IsSuccessStatusCode)
                {
                    logger.LogWarning("RAG briefing responded {Status}", response.StatusCode);
                    return Results.Ok(EmptyBriefing);
                }

                var body = await response.Content.ReadAsStringAsync(context.RequestAborted);
                cache.Set(cacheKey, body, BriefingCacheDuration);
                return Results.Content(body, "application/json");
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
            {
                // 브리핑 실패로 홈 화면을 막지 않는다. 위젯만 비어 보이고 채팅은 그대로 쓸 수 있다.
                logger.LogWarning(ex, "Failed to fetch home briefing from RAG service");
                return Results.Ok(EmptyBriefing);
            }
        });
    }
}
