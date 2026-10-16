using System.Net;
using System.Security.Claims;
using System.Text;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using RenuxServer.Apis;

internal static class NotificationCandidateScopeTests
{
    public static async Task RunAsync(Action<bool, string> check)
    {
        CheckScopeResolution(check);
        await CheckScopePassingAndCacheSeparationAsync(check);
    }

    private static void CheckScopeResolution(Action<bool, string> check)
    {
        var student = NotificationApis.ResolveCandidateScope(Context(new Claim("Major", "컴퓨터공학과")));
        check(student == new NotificationCandidateScope("컴퓨터공학과", false),
            "A logged-in student's candidate scope must carry the JWT Major claim and never allow WISE.");

        var unknown = NotificationApis.ResolveCandidateScope(Context(new Claim("Major", "Unknown")));
        check(unknown == NotificationCandidateScope.Public,
            "An Unknown major must fall back to the public-only scope.");

        var missing = NotificationApis.ResolveCandidateScope(Context());
        check(missing == NotificationCandidateScope.Public,
            "A missing Major claim must fall back to the public-only scope.");
    }

    private static async Task CheckScopePassingAndCacheSeparationAsync(Action<bool, string> check)
    {
        var handler = new RecordingHandler();
        var factory = new SingleClientFactory(handler);
        using var cache = new MemoryCache(new MemoryCacheOptions());
        IConfiguration configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["RagServiceUrl"] = "http://rag.test" })
            .Build();

        Task<List<NotificationCandidateDto>> Fetch(NotificationCandidateScope scope)
            => NotificationApis.FetchCandidatesAsync(
                configuration, factory, cache, NullLogger.Instance, scope, 60, 100, CancellationToken.None);

        var cs = await Fetch(new NotificationCandidateScope("컴퓨터공학과", false));
        var econ = await Fetch(new NotificationCandidateScope("경제학과", false));
        var guest = await Fetch(NotificationCandidateScope.Public);
        var wise = await Fetch(new NotificationCandidateScope("컴퓨터공학과", true));
        var csAgain = await Fetch(new NotificationCandidateScope("컴퓨터공학과", false));

        check(handler.Requests.Count == 4,
            $"Each distinct scope must miss the cache once and a repeated scope must hit it (requests={handler.Requests.Count}).");
        if (handler.Requests.Count < 4)
        {
            return;
        }

        var csQuery = Query(handler.Requests[0]);
        check(csQuery.GetValueOrDefault("major") == "컴퓨터공학과" && csQuery.GetValueOrDefault("allow_wise") == "false",
            "The student's major and allow_wise=false must be sent to the RAG candidate endpoint.");
        var econQuery = Query(handler.Requests[1]);
        check(econQuery.GetValueOrDefault("major") == "경제학과",
            "A different major must issue its own RAG request with that major.");
        var guestQuery = Query(handler.Requests[2]);
        check(!guestQuery.ContainsKey("major") && guestQuery.GetValueOrDefault("allow_wise") == "false",
            "The public scope must omit major and keep allow_wise=false.");
        var wiseQuery = Query(handler.Requests[3]);
        check(wiseQuery.GetValueOrDefault("allow_wise") == "true",
            "allow_wise must be forwarded when a caller explicitly allows WISE.");

        check(cs.Single().Title == "scope-1" && econ.Single().Title == "scope-2"
              && guest.Single().Title == "scope-3" && wise.Single().Title == "scope-4",
            "Scopes must never share cached candidate results.");
        check(csAgain.Single().Title == "scope-1",
            "The same scope must reuse its own cached candidates.");
    }

    private static Dictionary<string, string> Query(Uri uri)
        => uri.Query.TrimStart('?')
            .Split('&', StringSplitOptions.RemoveEmptyEntries)
            .Select(part => part.Split('=', 2))
            .ToDictionary(pair => Uri.UnescapeDataString(pair[0]), pair => pair.Length > 1 ? Uri.UnescapeDataString(pair[1]) : "");

    private static HttpContext Context(params Claim[] claims)
        => new DefaultHttpContext { User = new ClaimsPrincipal(new ClaimsIdentity(claims, "test")) };

    private sealed class RecordingHandler : HttpMessageHandler
    {
        public List<Uri> Requests { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Requests.Add(request.RequestUri!);
            var title = $"scope-{Requests.Count}";
            var json = $$"""
                [{"id":"notices:{{Requests.Count}}","source":"notices","source_id":"{{Requests.Count}}","chunk_id":null,"title":"{{title}}","topic":"scholarship","category":null,"target_date":"2026-07-10","start_date":null,"end_date":"2026-07-10","d_day":4,"published_at":null,"url":null,"snippet":"","confidence":0.78,"date_source":"apply_deadline_parsed","metadata":null}]
                """;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json"),
            });
        }
    }

    private sealed class SingleClientFactory(HttpMessageHandler handler) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => new(handler, disposeHandler: false);
    }
}
