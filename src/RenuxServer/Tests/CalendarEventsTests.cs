using System.Net;
using System.Security.Claims;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using RenuxServer.Apis;

internal static class CalendarEventsTests
{
    private static readonly JsonSerializerOptions WebJson = new(JsonSerializerDefaults.Web);

    public static async Task RunAsync(Action<bool, string> check)
    {
        await CheckValidationAsync(check);
        await CheckScopeAndQueryAsync(check);
        await CheckMappingAndOverlapAsync(check);
        await CheckRagFailureAsync(check);
        await CheckTruncationWarningAsync(check);
    }

    private static async Task CheckValidationAsync(Action<bool, string> check)
    {
        (string? From, string? To, string Label)[] invalid =
        [
            (null, "2026-10-31", "missing from"),
            ("2026-10-01", null, "missing to"),
            ("", "2026-10-31", "blank from"),
            ("2026/10/01", "2026-10-31", "malformed from"),
            ("2026-10-01", "2026-13-01", "invalid to"),
            ("2026-10-31", "2026-10-01", "to before from"),
            ("2026-10-01", "2027-01-02", "span over 92 days"),
        ];

        foreach (var (from, to, label) in invalid)
        {
            var harness = new Harness("[]");
            var outcome = await harness.GetAsync(Guest(), from, to);
            var body = Serialize(outcome.Body);
            check(outcome.StatusCode == 400
                  && body.TryGetProperty("error", out var error) && error.GetString() == "invalid_range"
                  && body.TryGetProperty("message", out _),
                $"Calendar validation must return a 400 JSON error for {label} (status={outcome.StatusCode}).");
            check(harness.Handler.Requests.Count == 0,
                $"Calendar validation failures must not call RAG ({label}).");
        }

        var edge = new Harness("[]");
        var edgeOutcome = await edge.GetAsync(Guest(), "2026-10-01", "2027-01-01");
        check(edgeOutcome.StatusCode == 200, "A span of exactly 92 days must be accepted.");
        var sameDay = new Harness("[]");
        check((await sameDay.GetAsync(Guest(), "2026-10-01", "2026-10-01")).StatusCode == 200,
            "from == to must be accepted (inclusive range).");
    }

    private static async Task CheckScopeAndQueryAsync(Action<bool, string> check)
    {
        var harness = new Harness("[]");
        await harness.GetAsync(Guest(), "2026-10-01", "2026-10-31");
        await harness.GetAsync(Student("컴퓨터공학과"), "2026-10-01", "2026-10-31");
        await harness.GetAsync(Student("Unknown"), "2026-10-01", "2026-10-31");
        await harness.GetAsync(Student("컴퓨터공학과"), "2026-10-01", "2026-10-31");

        check(harness.Handler.Requests.Count == 2,
            $"Guest/Unknown share the public cache entry and a repeated student scope must hit the cache (requests={harness.Handler.Requests.Count}).");
        if (harness.Handler.Requests.Count < 2)
        {
            return;
        }

        var guest = Query(harness.Handler.Requests[0]);
        check(!guest.ContainsKey("major") && guest.GetValueOrDefault("allow_wise") == "false",
            "A guest calendar request must be public-only with allow_wise=false.");
        check(guest.GetValueOrDefault("from") == "2026-10-01" && guest.GetValueOrDefault("to") == "2027-01-31",
            $"RAG must be queried from=from and to=to+92 days (got {guest.GetValueOrDefault("from")}..{guest.GetValueOrDefault("to")}).");
        check(guest.GetValueOrDefault("limit") == "300",
            "The calendar must request the RAG maximum of 300 candidates.");
        check(guest.GetValueOrDefault("sources") == "notices,schedule",
            "The calendar must request both schedule and notice candidates.");

        var student = Query(harness.Handler.Requests[1]);
        check(student.GetValueOrDefault("major") == "컴퓨터공학과" && student.GetValueOrDefault("allow_wise") == "false",
            "A logged-in student's calendar must pass the JWT major and allow_wise=false.");

        var unauthenticatedWithClaim = new DefaultHttpContext
        {
            User = new ClaimsPrincipal(new ClaimsIdentity([new Claim("Major", "컴퓨터공학과")])),
        };
        check(CalendarApis.ResolveScope(unauthenticatedWithClaim) == NotificationCandidateScope.Public,
            "An unauthenticated principal must never unlock a department scope.");
    }

    private static async Task CheckMappingAndOverlapAsync(Action<bool, string> check)
    {
        var json = "[" + string.Join(",",
            // 긴 기간: 기간 안에서 시작해 to 이후에 끝나는 겨울 방학.
            // 긴 기간: from 이전에 시작해 기간 안에서 끝나는 일정.
            Candidate("schedule:span", "schedule", "기간 걸침", target: "2026-12-03", start: "2026-11-20", end: "2026-12-03"),
            Candidate("schedule:winter", "schedule", "겨울 방학", target: "2027-02-28", start: "2026-12-22", end: "2027-02-28"),
            Candidate("schedule:exam", "schedule", "기말시험", target: "2026-12-18", start: "2026-12-14", end: "2026-12-18"),
            Candidate("schedule:reg", "schedule", "수강신청", target: "2026-12-05", start: "2026-12-01", end: "2026-12-05",
                extra: "\"date_status\":\"unverified\",\"evidence\":\"학사일정표 12월\""),
            Candidate("notices:42", "notices", "장학금 신청", target: "2026-12-10", end: null,
                extra: "\"published_at\":\"2026-11-20\",\"metadata\":{\"department\":\"학생지원팀\",\"evidence\":\"신청기간: ~12.10\",\"date_status\":\"confirmed\"}"),
            Candidate("notices:42", "notices", "장학금 신청 (중복)", target: "2026-12-10", end: "2026-12-10"),
            Candidate("notices:odd", "notices", "가 공지", target: "2026-12-10", end: "2026-12-10",
                extra: "\"date_status\":\"inferred\""),
            // 기간 밖: 시작이 to 이후.
            Candidate("schedule:next", "schedule", "신년 행사", target: "2027-01-05", start: "2027-01-02", end: "2027-01-05"),
            // 기간 밖: from 이전에 끝남.
            Candidate("notices:old", "notices", "지난 공지", target: "2026-11-30", end: "2026-11-30"),
            // 경계: 마지막 날 시작 / 첫날 종료.
            Candidate("schedule:edge-end", "schedule", "종강", target: "2027-01-10", start: "2026-12-31", end: "2027-01-10"),
            Candidate("notices:edge-start", "notices", "첫날 마감", target: "2026-12-01", end: "2026-12-01"),
            Candidate("schedule:bad", "schedule", "날짜 없음", target: "unknown", end: null)) + "]";

        var harness = new Harness(json);
        var outcome = await harness.GetAsync(Guest(), "2026-12-01", "2026-12-31");
        check(outcome.StatusCode == 200, $"A successful RAG response must return 200 (status={outcome.StatusCode}).");
        var body = Serialize(outcome.Body);
        check(body.GetProperty("from").GetString() == "2026-12-01" && body.GetProperty("to").GetString() == "2026-12-31"
              && DateTimeOffset.TryParse(body.GetProperty("generatedAt").GetString(), out _),
            "The calendar response must echo from/to and include an ISO-8601 generatedAt.");

        var events = body.GetProperty("events").EnumerateArray().ToList();
        var ids = events.Select(e => e.GetProperty("id").GetString()).ToList();
        string[] expectedOrder =
        [
            "schedule:span",       // 11-20 시작, from 이전
            "schedule:reg",        // 12-01 수강신청 (ordinal: 수 < 첫)
            "notices:edge-start",  // 12-01 첫날 마감
            "notices:odd",         // 12-10 가 공지
            "notices:42",          // 12-10 장학금 신청
            "schedule:exam",       // 12-14
            "schedule:winter",     // 12-22
            "schedule:edge-end",   // 12-31
        ];
        check(ids.SequenceEqual(expectedOrder),
            $"Events must be overlap-filtered, de-duplicated and sorted by (startDate ?? endDate, title). Got: {string.Join(", ", ids)}");

        JsonElement Event(string id) => events.First(e => e.GetProperty("id").GetString() == id);

        if (ids.Contains("schedule:winter"))
        {
            var winter = Event("schedule:winter");
            check(winter.GetProperty("kind").GetString() == "academic"
                  && winter.GetProperty("startDate").GetString() == "2026-12-22"
                  && winter.GetProperty("endDate").GetString() == "2027-02-28",
                "A long period ending after `to` must still appear with its own start/end dates.");
        }

        if (ids.Contains("schedule:span"))
        {
            var span = Event("schedule:span");
            check(span.GetProperty("startDate").GetString() == "2026-11-20" && span.GetProperty("endDate").GetString() == "2026-12-03",
                "A period starting before `from` and ending inside the range must appear with its original startDate.");
        }

        if (ids.Contains("schedule:exam"))
        {
            check(Event("schedule:exam").GetProperty("kind").GetString() == "exam",
                "Schedule titles containing 시험 must map to kind=exam.");
        }

        if (ids.Contains("notices:42"))
        {
            var notice = Event("notices:42");
            check(notice.GetProperty("kind").GetString() == "notice"
                  && notice.GetProperty("source").GetString() == "notices"
                  && notice.GetProperty("title").GetString() == "장학금 신청",
                "Notices must map to kind=notice and the first occurrence must win de-duplication.");
            check(notice.GetProperty("startDate").ValueKind == JsonValueKind.Null
                  && notice.GetProperty("endDate").GetString() == "2026-12-10",
                "endDate must fall back to target_date and a notice startDate must be null.");
            check(notice.GetProperty("department").GetString() == "학생지원팀"
                  && notice.GetProperty("evidence").GetString() == "신청기간: ~12.10"
                  && notice.GetProperty("dateStatus").GetString() == "confirmed"
                  && notice.GetProperty("publishedAt").GetString() == "2026-11-20",
                "department/evidence/date_status must pass through from RAG metadata.");
            check(notice.GetProperty("url").GetString() == "https://www.dongguk.edu/notices:42",
                "The original source URL must be preserved.");
        }

        if (ids.Contains("schedule:reg"))
        {
            var reg = Event("schedule:reg");
            check(reg.GetProperty("dateStatus").GetString() == "unverified"
                  && reg.GetProperty("evidence").GetString() == "학사일정표 12월",
                "Top-level evidence/date_status fields must pass through.");
        }

        if (ids.Contains("schedule:winter"))
        {
            var winter = Event("schedule:winter");
            check(winter.GetProperty("dateStatus").GetString() == "confirmed"
                  && winter.GetProperty("evidence").ValueKind == JsonValueKind.Null
                  && winter.GetProperty("department").ValueKind == JsonValueKind.Null
                  && winter.GetProperty("snippet").ValueKind == JsonValueKind.Null,
                "Missing evidence/department must be null, a blank snippet null, and a missing date_status default to confirmed.");
        }

        if (ids.Contains("notices:odd"))
        {
            check(Event("notices:odd").GetProperty("dateStatus").GetString() == "unverified",
                "An unknown date_status must never be reported as confirmed.");
        }

        var expectedKeys = new[]
        {
            "id", "source", "kind", "title", "startDate", "endDate", "url", "snippet",
            "evidence", "dateStatus", "department", "publishedAt",
        };
        check(events.Count > 0 && events.All(e => e.EnumerateObject().Select(p => p.Name).OrderBy(n => n)
                .SequenceEqual(expectedKeys.OrderBy(n => n))),
            "Every event must expose exactly the camelCase contract fields.");
    }

    private static async Task CheckRagFailureAsync(Action<bool, string> check)
    {
        var errorHarness = new Harness("[]", HttpStatusCode.InternalServerError);
        var outcome = await errorHarness.GetAsync(Guest(), "2026-10-01", "2026-10-31");
        var body = Serialize(outcome.Body);
        check(outcome.StatusCode == 503
              && body.TryGetProperty("error", out var error) && error.GetString() == "calendar_unavailable"
              && !body.TryGetProperty("events", out _),
            $"A RAG HTTP failure must return a 503 JSON error, never an empty success (status={outcome.StatusCode}).");

        var malformed = new Harness("{not json");
        var malformedOutcome = await malformed.GetAsync(Guest(), "2026-10-01", "2026-10-31");
        check(malformedOutcome.StatusCode == 503, "A malformed RAG response must return 503.");

        // 알림 경로는 기존처럼 실패 시 빈 목록으로 진행해야 한다.
        var notificationCandidates = await NotificationApis.FetchCandidatesAsync(
            errorHarness.Configuration, errorHarness.Factory, errorHarness.Cache, errorHarness.Logger,
            NotificationCandidateScope.Public, 60, 100, CancellationToken.None);
        check(notificationCandidates.Count == 0,
            "The notification path must keep returning an empty list when RAG fails.");
    }

    private static async Task CheckTruncationWarningAsync(Action<bool, string> check)
    {
        var many = "[" + string.Join(",", Enumerable.Range(0, 300)
            .Select(i => Candidate($"notices:{i}", "notices", $"공지 {i:D3}", target: "2026-10-15", end: "2026-10-15"))) + "]";
        var harness = new Harness(many);
        var outcome = await harness.GetAsync(Guest(), "2026-10-01", "2026-10-31");
        check(outcome.StatusCode == 200 && Serialize(outcome.Body).GetProperty("events").GetArrayLength() == 300,
            "A full page of 300 candidates must still be returned.");
        check(harness.Logger.Warnings.Any(message => message.Contains("truncated", StringComparison.Ordinal)),
            "Hitting the RAG limit of 300 must log a possible-truncation warning.");

        var few = new Harness("[" + Candidate("notices:1", "notices", "공지", target: "2026-10-15", end: "2026-10-15") + "]");
        await few.GetAsync(Guest(), "2026-10-01", "2026-10-31");
        check(!few.Logger.Warnings.Any(message => message.Contains("truncated", StringComparison.Ordinal)),
            "A short candidate list must not log a truncation warning.");
    }

    private static string Candidate(
        string id,
        string source,
        string title,
        string target,
        string? end,
        string? start = null,
        string? extra = null)
    {
        string Str(string? value) => value is null ? "null" : JsonSerializer.Serialize(value);
        var json = $$"""
            {"id":{{Str(id)}},"source":{{Str(source)}},"source_id":{{Str(id.Split(':')[1])}},"chunk_id":null,"title":{{Str(title)}},"topic":"academic_schedule","category":null,"target_date":{{Str(target)}},"start_date":{{Str(start)}},"end_date":{{Str(end)}},"d_day":10,"url":{{Str("https://www.dongguk.edu/" + id)}},"snippet":" ","confidence":0.9,"date_source":"schedule"
            """;
        if (!extra?.Contains("\"published_at\"", StringComparison.Ordinal) ?? true)
        {
            json += ",\"published_at\":null";
        }
        if (!extra?.Contains("\"metadata\"", StringComparison.Ordinal) ?? true)
        {
            json += ",\"metadata\":{}";
        }
        if (extra is not null)
        {
            json += "," + extra;
        }
        return json + "}";
    }

    private static JsonElement Serialize(object body)
        => JsonSerializer.SerializeToElement(body, body.GetType(), WebJson);

    private static Dictionary<string, string> Query(Uri uri)
        => uri.Query.TrimStart('?')
            .Split('&', StringSplitOptions.RemoveEmptyEntries)
            .Select(part => part.Split('=', 2))
            .ToDictionary(pair => Uri.UnescapeDataString(pair[0]), pair => pair.Length > 1 ? Uri.UnescapeDataString(pair[1]) : "");

    private static HttpContext Guest() => new DefaultHttpContext();

    private static HttpContext Student(string major)
        => new DefaultHttpContext
        {
            User = new ClaimsPrincipal(new ClaimsIdentity([new Claim("Major", major)], "test")),
        };

    private sealed class Harness
    {
        public Harness(string json, HttpStatusCode status = HttpStatusCode.OK)
        {
            Handler = new StaticHandler(json, status);
            Factory = new SingleClientFactory(Handler);
        }

        public StaticHandler Handler { get; }
        public IHttpClientFactory Factory { get; }
        public MemoryCache Cache { get; } = new(new MemoryCacheOptions());
        public RecordingLogger Logger { get; } = new();
        public IConfiguration Configuration { get; } = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["RagServiceUrl"] = "http://rag.test" })
            .Build();

        public Task<CalendarEventsOutcome> GetAsync(HttpContext context, string? from, string? to)
            => CalendarApis.GetEventsAsync(context, Configuration, Factory, Cache, Logger, from, to, CancellationToken.None);
    }

    private sealed class StaticHandler(string json, HttpStatusCode status) : HttpMessageHandler
    {
        public List<Uri> Requests { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Requests.Add(request.RequestUri!);
            return Task.FromResult(new HttpResponseMessage(status)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json"),
            });
        }
    }

    private sealed class SingleClientFactory(HttpMessageHandler handler) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => new(handler, disposeHandler: false);
    }

    private sealed class RecordingLogger : ILogger
    {
        public List<string> Warnings { get; } = [];

        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

        public bool IsEnabled(LogLevel logLevel) => true;

        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
        {
            if (logLevel == LogLevel.Warning)
            {
                Warnings.Add(formatter(state, exception));
            }
        }
    }
}
