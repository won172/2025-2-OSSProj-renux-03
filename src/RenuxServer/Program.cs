using AutoMapper;
using Microsoft.EntityFrameworkCore;
using FluentValidation;
using Microsoft.AspNetCore.Http;

using RenuxServer.DbContexts;
using RenuxServer.Dtos.AuthDtos;
using RenuxServer.Middlewares;
using RenuxServer.Validators;
using RenuxServer.Apis.Auth;

using Microsoft.IdentityModel.Tokens;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.AspNetCore.HttpOverrides;
using RenuxServer.Models;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Dtos.EtcDtos;
using RenuxServer.Apis;
using RenuxServer.Apis.Chat;

var builder = WebApplication.CreateBuilder();

builder.Configuration.AddUserSecrets<Program>();

var rawConnectionString =
    builder.Configuration.GetConnectionString("RenuxServer")
    ?? builder.Configuration["CONNECTIONSTRING"];

if (string.IsNullOrWhiteSpace(rawConnectionString))
{
    throw new InvalidOperationException("Database connection string is not configured. Set ConnectionStrings__RenuxServer or CONNECTIONSTRING.");
}

var rawJwtKey =
    builder.Configuration["Jwt:Key"]
    ?? builder.Configuration["JWT_KEY"];

if (string.IsNullOrWhiteSpace(rawJwtKey))
{
    throw new InvalidOperationException("JWT signing key is not configured. Set Jwt__Key or JWT_KEY.");
}

var configuredCorsOrigins = (builder.Configuration["CORS_ALLOWED_ORIGINS"] ?? string.Empty)
    .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

// DbContext Setting
builder.Services.AddDbContext<ServerDbContext>(options =>
{
    // 일시적 DB 단절(컨테이너 재시작 경쟁 등)에 자동 재시도 — 즉시 500 반환 방지.
    options.UseNpgsql(rawConnectionString, npgsql => npgsql.EnableRetryOnFailure(3));
});

// AutoMapper Setting
builder.Services.AddAutoMapper(options =>
{
    options.CreateMap<SignupUserDto, User>();
    options.CreateMap<ActiveChat, ActiveChatDto>();
    options.CreateMap<ChatMessage, ChatMessageDto>();
    options.CreateMap<ChatMessageDto, ChatMessage>();
    options.CreateMap<Major, MajorDto>();
    options.CreateMap<Role, RoleDto>();
    options.CreateMap<Organization, OrganizationDto>();
});


// Validator Setting
builder.Services.AddValidatorsFromAssemblyContaining<SignupUserValidator>();
builder.Services.AddValidatorsFromAssemblyContaining<SigninUserValidator>();


// Auth Setting
builder.Services.AddAuthentication(options =>
    {
        options.DefaultAuthenticateScheme = JwtBearerDefaults.AuthenticationScheme;
        options.DefaultChallengeScheme = JwtBearerDefaults.AuthenticationScheme;
        options.DefaultScheme = JwtBearerDefaults.AuthenticationScheme;
    })
    .AddJwtBearer(options =>
    {
        options.MapInboundClaims = false; // Disable automatic claim mapping
        options.TokenValidationParameters = new()
        {
            IssuerSigningKey = new SymmetricSecurityKey(Convert.FromBase64String(rawJwtKey)),
            ValidateIssuer = false,
            ValidateAudience = false,
            ValidateLifetime = true,
            // 기본 5분 유예 제거 — 만료 토큰이 추가로 유효해지는 창을 없앤다.
            ClockSkew = TimeSpan.Zero
        };

        options.Events = new()
        {
            OnMessageReceived = context =>
            {
                if (context.Request.Cookies.ContainsKey("renux-server-token"))
                {
                    context.Token = context.Request.Cookies["renux-server-token"];
                    Console.WriteLine(">> [Auth] Token cookie found.");
                }
                else
                {
                    Console.WriteLine(">> [Auth] No token cookie.");
                }
                return Task.CompletedTask;
            },
            OnAuthenticationFailed = context =>
            {
                Console.WriteLine($">> [Auth] Failed: {context.Exception.Message}");
                return Task.CompletedTask;
            }
        };
    });

builder.Services.AddAuthorization();
builder.Services.AddHttpClient();

// 인증/회원가입 엔드포인트 무차별 대입·열거 방어용 레이트 리미터 (IP 단위 고정 윈도)
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    options.AddPolicy("auth", httpContext =>
        System.Threading.RateLimiting.RateLimitPartition.GetFixedWindowLimiter(
            partitionKey: httpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown",
            factory: _ => new System.Threading.RateLimiting.FixedWindowRateLimiterOptions
            {
                PermitLimit = 20,
                Window = TimeSpan.FromMinutes(1),
                QueueLimit = 0,
            }));

    // 채팅(RAG) 경로 IP 단위 제한 — LLM 토큰 비용 폭주·남용 방어.
    options.AddPolicy("chat", httpContext =>
        System.Threading.RateLimiting.RateLimitPartition.GetFixedWindowLimiter(
            partitionKey: httpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown",
            factory: _ => new System.Threading.RateLimiting.FixedWindowRateLimiterOptions
            {
                PermitLimit = 30,
                Window = TimeSpan.FromMinutes(1),
                QueueLimit = 0,
            }));
});

// CORS is credentialed (cookies), so the origin allowlist must be tight.
// Production must set CORS_ALLOWED_ORIGINS explicitly (e.g. https://dgudongttok.vercel.app);
// localhost is only permitted in Development. No wildcard origins.
var isDevelopment = builder.Environment.IsDevelopment();
builder.Services.AddCors(options =>
{
    options.AddPolicy("FrontendCors", policy =>
    {
        policy
            .SetIsOriginAllowed(origin =>
            {
                // Explicit allowlist always wins.
                if (configuredCorsOrigins.Contains(origin, StringComparer.OrdinalIgnoreCase))
                {
                    return true;
                }

                // Local development convenience only — never in production.
                if (isDevelopment &&
                    (origin.StartsWith("http://localhost:", StringComparison.OrdinalIgnoreCase) ||
                     origin.StartsWith("https://localhost:", StringComparison.OrdinalIgnoreCase) ||
                     origin.StartsWith("http://127.0.0.1:", StringComparison.OrdinalIgnoreCase) ||
                     origin.StartsWith("https://127.0.0.1:", StringComparison.OrdinalIgnoreCase)))
                {
                    return true;
                }

                return false;
            })
            .AllowAnyHeader()
            .AllowAnyMethod()
            .AllowCredentials();
    });
});

var app = builder.Build();

// nginx 등 리버스 프록시가 전달하는 X-Forwarded-For/Proto를 신뢰해 실제 클라이언트 IP/스킴을
// 복원한다. 이게 없으면 레이트 리미터가 프록시(컨테이너) IP로 집계돼 IP별 제한이 무력화된다.
// 단일 ingress(nginx)만 존재하므로 KnownNetworks/Proxies를 비워 프록시를 신뢰한다.
var forwardedHeadersOptions = new ForwardedHeadersOptions
{
    ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto
};
forwardedHeadersOptions.KnownNetworks.Clear();
forwardedHeadersOptions.KnownProxies.Clear();
app.UseForwardedHeaders(forwardedHeadersOptions);

app.UseMiddleware<GlobalExceptionHandlerMiddleware>();
app.UseCors("FrontendCors");

using (var scope = app.Services.CreateScope())
{
    try
    {
        var db = scope.ServiceProvider.GetRequiredService<ServerDbContext>();
        // sources_json / is_fallback / fallback_reason 컬럼은 정식 EF 마이그레이션
        // (AddChatMessageSources, AddChatMessageFallbackReason)으로 흡수됨.
        await db.Database.MigrateAsync();

        List<Major> majors = await db.Majors.ToListAsync();

        foreach(var m in majors)
        {
            if (!await db.Organizations.AnyAsync(o => o.MajorId == m.Id))
                await db.Organizations.AddAsync(new() { IsActive = true, Major = m });
        }

        await db.SaveChangesAsync();
    }
    catch(Exception e)
    {
        Console.Error.WriteLine(e.ToString());
        throw;
    }
}


app.UseRateLimiter();

app.UseAuthentication();
app.UseAuthorization();

app.UseStaticFiles();

app.AddAuthApis();
app.AddChatApis();
app.AddEtcApis();
app.AddAdminProxyApis();

// 헬스체크: /health = 프로세스 생존(liveness), /ready = DB 연결 확인(readiness).
// 컨테이너 오케스트레이션(docker healthcheck / fly checks)이 트래픽 라우팅·롤백 판단에 사용.
app.MapGet("/health", () => Results.Ok(new { status = "ok" })).AllowAnonymous();
app.MapGet("/ready", async (ServerDbContext db, CancellationToken ct) =>
{
    try
    {
        var canConnect = await db.Database.CanConnectAsync(ct);
        return canConnect
            ? Results.Ok(new { status = "ready" })
            : Results.Json(new { status = "db_unavailable" }, statusCode: 503);
    }
    catch
    {
        return Results.Json(new { status = "db_unavailable" }, statusCode: 503);
    }
}).AllowAnonymous();

app.MapGet("/", async (HttpContext context) =>
{
    context.Response.ContentType = "text/html";
    await context.Response.SendFileAsync("wwwroot/index.html");
});

app.MapGet("/notifications", () => Results.Ok(Array.Empty<object>()));
app.MapMethods("/notifications", new[] { "OPTIONS" }, () => Results.Ok());

// SPA fallback for client-side routing
app.MapFallbackToFile("index.html");

app.Run();
