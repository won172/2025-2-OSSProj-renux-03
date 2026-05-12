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
builder.Services.AddCors(options =>
{
    options.AddPolicy("FrontendCors", policy =>
    {
        policy
            .SetIsOriginAllowed(origin =>
            {
                if (configuredCorsOrigins.Length > 0)
                {
                    return configuredCorsOrigins.Contains(origin, StringComparer.OrdinalIgnoreCase);
                }

                if (origin.StartsWith("http://localhost:", StringComparison.OrdinalIgnoreCase) ||
                    origin.StartsWith("https://localhost:", StringComparison.OrdinalIgnoreCase) ||
                    origin.StartsWith("http://127.0.0.1:", StringComparison.OrdinalIgnoreCase) ||
                    origin.StartsWith("https://127.0.0.1:", StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }

                if (Uri.TryCreate(origin, UriKind.Absolute, out var uri))
                {
                    return uri.Host.EndsWith(".vercel.app", StringComparison.OrdinalIgnoreCase);
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
        await db.Database.MigrateAsync();
        await db.Database.ExecuteSqlRawAsync("""
            ALTER TABLE chat_messages
            ADD COLUMN IF NOT EXISTS sources_json text;
            """);
        await db.Database.ExecuteSqlRawAsync("""
            ALTER TABLE chat_messages
            ADD COLUMN IF NOT EXISTS is_fallback boolean NOT NULL DEFAULT FALSE;
            """);
        await db.Database.ExecuteSqlRawAsync("""
            ALTER TABLE chat_messages
            ADD COLUMN IF NOT EXISTS fallback_reason text;
            """);

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
        Console.WriteLine(e.Message);
        return;
    }
}


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
