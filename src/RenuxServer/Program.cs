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
    options.UseNpgsql(rawConnectionString);
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
            ValidateAudience = false
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
