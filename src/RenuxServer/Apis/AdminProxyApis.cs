using Microsoft.EntityFrameworkCore;
using RenuxServer.DbContexts;
using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text.Json;

namespace RenuxServer.Apis;

static public class AdminProxyApis
{
    // 학과 수준 관리자(지식 제출/목록 조회)까지 허용되는 역할
    private static readonly HashSet<string> DepartmentLevelRoles = new(StringComparer.OrdinalIgnoreCase)
    {
        "관리자",
        "총학생회",
        "학생회",
    };

    // 승인/거절/로그 등 대학 수준 관리 기능에만 허용되는 역할
    private static readonly HashSet<string> UniversityLevelRoles = new(StringComparer.OrdinalIgnoreCase)
    {
        "관리자",
        "총학생회",
    };

    // 역할은 JWT 클레임이 아닌 DB에서 읽는다 — 역할 강등/변경이 토큰 만료(60분)를
    // 기다리지 않고 즉시 admin API에 반영되도록.
    private static async ValueTask<object?> EnforceDbRoleAsync(
        EndpointFilterInvocationContext context,
        EndpointFilterDelegate next,
        IReadOnlySet<string> allowedRoles)
    {
        var http = context.HttpContext;
        var user = http.User;
        if (user?.Identity?.IsAuthenticated != true)
        {
            return Results.Unauthorized();
        }

        var sub = user.FindFirstValue(JwtRegisteredClaimNames.Sub);
        if (!Guid.TryParse(sub, out Guid userId))
        {
            return Results.Unauthorized();
        }

        var db = http.RequestServices.GetRequiredService<ServerDbContext>();
        var roleName = await db.Users
            .Where(u => u.Id == userId)
            .Select(u => u.Role!.Rolename)
            .FirstOrDefaultAsync();

        if (string.IsNullOrWhiteSpace(roleName) || !allowedRoles.Contains(roleName))
        {
            return Results.Forbid();
        }

        return await next(context);
    }

    static public void AddAdminProxyApis(this WebApplication application)
    {
        var app = application.MapGroup("/admin").RequireAuthorization();
        string RagServiceUrl = application.Configuration["RagServiceUrl"] ?? application.Configuration["RAG_SERVICE_URL"] ?? "http://rag-service:8000";

        // 그룹 공통: 최소 학과 수준 관리자 역할(DB 기준) 필요
        app.AddEndpointFilter((context, next) => EnforceDbRoleAsync(context, next, DepartmentLevelRoles));

        // 대학 수준 전용 엔드포인트에 덧씌우는 필터
        static TBuilder RequireUniversityLevel<TBuilder>(TBuilder builder) where TBuilder : IEndpointConventionBuilder
        {
            builder.AddEndpointFilter((context, next) => EnforceDbRoleAsync(context, next, UniversityLevelRoles));
            return builder;
        }

        RequireUniversityLevel(app.MapGet("pending", async (HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) =>
        {
            logger.LogInformation("Proxying /admin/pending to {Url}/admin/pending", RagServiceUrl);
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/pending");
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        }));

        // 지식 항목 목록. RAG /admin/items는 전 학과 항목을 반환하므로(요청자 구분 불가),
        // 학과 수준 관리자에게는 본인 학과 항목만 보이도록 이 계층에서 필터한다.
        // 대학 수준 관리자(승인 권한)는 전체를 본다.
        app.MapGet("items", async (HttpContext context, ServerDbContext db, IHttpClientFactory httpClientFactory, ILogger<Program> logger) =>
        {
            logger.LogInformation("Proxying /admin/items to {Url}/admin/items", RagServiceUrl);
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/items");
            var body = await proxyRes.Content.ReadAsStringAsync();
            if (!proxyRes.IsSuccessStatusCode)
            {
                return Results.Content(body, "application/json", statusCode: (int)proxyRes.StatusCode);
            }

            // 호출자의 역할/학과 조회 (역할 허용 여부는 그룹 필터에서 이미 검증됨)
            var sub = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
            if (Guid.TryParse(sub, out Guid userId))
            {
                var caller = await db.Users
                    .Where(u => u.Id == userId)
                    .Select(u => new { Role = u.Role!.Rolename, Major = u.Major!.Majorname })
                    .FirstOrDefaultAsync();

                var isUniversityLevel = caller != null && UniversityLevelRoles.Contains(caller.Role);
                if (!isUniversityLevel)
                {
                    // 학과 관리자: 본인 학과 항목만 (학과 식별 불가 시 fail-closed)
                    body = FilterItemsByDepartment(body, caller?.Major, logger);
                }
            }
            else
            {
                body = "[]"; // 사용자 식별 실패 시 fail-closed
            }

            return Results.Content(body, "application/json", statusCode: (int)proxyRes.StatusCode);
        });

        RequireUniversityLevel(app.MapGet("rag/status", async (HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) =>
        {
            logger.LogInformation("Proxying /admin/rag/status to {Url}/admin/rag/status", RagServiceUrl);
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/rag/status");
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        }));

        RequireUniversityLevel(app.MapGet("rag-logs-list", async (HttpRequest request, HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) =>
        {
            string url = $"{RagServiceUrl}/admin/rag/logs{request.QueryString}";
            logger.LogInformation("Proxying /admin/rag-logs-list to {Url}", url);
            try
            {
                var client = httpClientFactory.CreateClient();
                var proxyRes = await client.GetAsync(url);
                logger.LogInformation("RAG service response: {Status}", proxyRes.StatusCode);
                response.StatusCode = (int)proxyRes.StatusCode;
                var contentStream = await proxyRes.Content.ReadAsStreamAsync();
                return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Error proxying to RAG service at {Url}", url);
                // 내부 URL/예외 메시지는 응답에 노출하지 않는다 (GlobalExceptionHandler 정책과 동일)
                return Results.Problem(detail: "RAG 서비스 연결에 실패했습니다.", statusCode: 500);
            }
        }));

        RequireUniversityLevel(app.MapGet("rag-logs/export", async (HttpRequest request, HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) =>
        {
            logger.LogInformation("Proxying /admin/rag-logs/export to {Url}/admin/rag-logs/export", RagServiceUrl);
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/rag-logs/export{request.QueryString}");
            response.StatusCode = (int)proxyRes.StatusCode;
            if (proxyRes.Content.Headers.ContentDisposition != null)
            {
                response.Headers.ContentDisposition = proxyRes.Content.Headers.ContentDisposition.ToString();
            }
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "text/csv");
        }));

        app.MapPost("/submit", async (HttpRequest request, HttpResponse response, IHttpClientFactory httpClientFactory) =>
        {
            var client = httpClientFactory.CreateClient();
            using var streamContent = new StreamContent(request.Body);
            if (request.ContentType != null)
            {
                streamContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(request.ContentType);
            }

            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/submit", streamContent);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

        // 제출자(학과 관리자)가 '본인 학과의 대기 중(PENDING)' 요청을 직접 취소한다.
        // reject는 대학 수준 전용이라, 이 엔드포인트가 없으면 학과 관리자는 자기 요청도 못 거둔다(403).
        // 대학 수준 관리자는 제한 없이 취소 가능.
        app.MapPost("/cancel/{id}", async (int id, HttpContext context, ServerDbContext db, HttpResponse response, IHttpClientFactory httpClientFactory) =>
        {
            var client = httpClientFactory.CreateClient();

            // 1. 대상 항목의 상태/학과 확인
            var itemsRes = await client.GetAsync($"{RagServiceUrl}/admin/items");
            if (!itemsRes.IsSuccessStatusCode)
            {
                return Results.Problem(detail: "요청 목록을 확인하지 못했습니다.", statusCode: 502);
            }
            var itemsBody = await itemsRes.Content.ReadAsStringAsync();
            if (!TryGetItem(itemsBody, id, out var status, out var itemDepartment))
            {
                return Results.NotFound(new { message = "요청을 찾을 수 없습니다." });
            }

            // 2. 호출자 역할/학과 (역할 허용은 그룹 필터에서 이미 검증됨)
            var sub = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
            if (!Guid.TryParse(sub, out Guid userId))
            {
                return Results.Unauthorized();
            }
            var caller = await db.Users
                .Where(u => u.Id == userId)
                .Select(u => new { Role = u.Role!.Rolename, Major = u.Major!.Majorname })
                .FirstOrDefaultAsync();
            var isUniversityLevel = caller != null && UniversityLevelRoles.Contains(caller.Role);

            // 3. 학과 관리자는 '본인 학과의 PENDING'만 취소 가능
            if (!isUniversityLevel)
            {
                if (!string.Equals(status, "pending", StringComparison.OrdinalIgnoreCase))
                {
                    return Results.Json(new { message = "대기 중인 요청만 취소할 수 있습니다." }, statusCode: 409);
                }
                if (string.IsNullOrWhiteSpace(caller?.Major) || !string.Equals(itemDepartment, caller!.Major, StringComparison.Ordinal))
                {
                    return Results.Json(new { message = "본인 학과의 요청만 취소할 수 있습니다." }, statusCode: 403);
                }
            }

            // 4. RAG reject로 위임 (PENDING이면 색인 부작용 없이 상태만 변경)
            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/reject/{id}", null);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

        RequireUniversityLevel(app.MapPost("/approve/{id}", async (int id, HttpResponse response, IHttpClientFactory httpClientFactory) =>
        {
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/approve/{id}", null);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        }));

        RequireUniversityLevel(app.MapPost("/reject/{id}", async (int id, HttpResponse response, IHttpClientFactory httpClientFactory) =>
        {
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/reject/{id}", null);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        }));
    }

    // RAG가 돌려준 항목 배열(JSON)을 호출자 학과 항목만 남기도록 필터한다.
    // 파싱 실패·학과 미상 시 빈 목록을 반환하여 타 학과 데이터가 노출되지 않게 한다(fail-closed).
    private static string FilterItemsByDepartment(string json, string? department, ILogger logger)
    {
        if (string.IsNullOrWhiteSpace(department))
        {
            return "[]";
        }
        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind != JsonValueKind.Array)
            {
                return "[]";
            }
            var kept = new List<JsonElement>();
            foreach (var item in doc.RootElement.EnumerateArray())
            {
                if (ItemBelongsToDepartment(item, department))
                {
                    kept.Add(item.Clone());
                }
            }
            return JsonSerializer.Serialize(kept);
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "admin/items 학과 필터링 실패 — 안전하게 빈 목록 반환");
            return "[]";
        }
    }

    // 항목의 data(JSON 문자열)에서 학과 식별자를 추출한다.
    // 제출 페이로드가 유형별로 다르게 저장하므로(지식=category, 행사/공지=department) 순서대로 확인한다.
    private static string ExtractItemDepartment(JsonElement item)
    {
        if (!item.TryGetProperty("data", out var dataProp) || dataProp.ValueKind != JsonValueKind.String)
        {
            return "";
        }
        var dataStr = dataProp.GetString();
        if (string.IsNullOrWhiteSpace(dataStr))
        {
            return "";
        }
        try
        {
            using var inner = JsonDocument.Parse(dataStr);
            var root = inner.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                return "";
            }
            foreach (var key in new[] { "department", "category" })
            {
                if (root.TryGetProperty(key, out var value)
                    && value.ValueKind == JsonValueKind.String
                    && !string.IsNullOrWhiteSpace(value.GetString()))
                {
                    return value.GetString()!.Trim();
                }
            }
        }
        catch
        {
            return "";
        }
        return "";
    }

    private static bool ItemBelongsToDepartment(JsonElement item, string department)
        => string.Equals(ExtractItemDepartment(item), department, StringComparison.Ordinal);

    // items 목록(JSON)에서 특정 id 항목의 상태와 학과를 찾는다(취소 권한 검증용).
    private static bool TryGetItem(string json, int id, out string status, out string department)
    {
        status = "";
        department = "";
        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind != JsonValueKind.Array)
            {
                return false;
            }
            foreach (var item in doc.RootElement.EnumerateArray())
            {
                if (!item.TryGetProperty("id", out var idProp) || idProp.ValueKind != JsonValueKind.Number || idProp.GetInt32() != id)
                {
                    continue;
                }
                if (item.TryGetProperty("status", out var s) && s.ValueKind == JsonValueKind.String)
                {
                    status = s.GetString() ?? "";
                }
                department = ExtractItemDepartment(item);
                return true;
            }
        }
        catch
        {
            return false;
        }
        return false;
    }
}
