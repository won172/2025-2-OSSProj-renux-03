using FluentValidation;
using AutoMapper;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;

using RenuxServer.DbContexts;
using RenuxServer.Dtos.AuthDtos;
using RenuxServer.Models;
using System.Security.Claims;
using System.IdentityModel.Tokens.Jwt;

namespace RenuxServer.Apis.Auth;

public record IdCheck(string Id);

static public class AuthenticationApis
{
    private const string CouncilSignupInstagramUrl = "https://www.instagram.com/dongttok.dgu?igsh=MWs3MWJ4OWU3NjdlMw%3D%3D&utm_source=qr";

    private static readonly HashSet<string> UniversityLevelRoles = new(StringComparer.OrdinalIgnoreCase)
    {
        "관리자",
        "총학생회",
    };

    private static async ValueTask<object?> EnforceUniversityLevelAsync(
        EndpointFilterInvocationContext context,
        EndpointFilterDelegate next)
    {
        var http = context.HttpContext;
        if (http.User?.Identity?.IsAuthenticated != true)
        {
            return Results.Unauthorized();
        }

        var sub = http.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
        if (!Guid.TryParse(sub, out Guid userId))
        {
            return Results.Unauthorized();
        }

        var db = http.RequestServices.GetRequiredService<ServerDbContext>();
        var roleName = await db.Users
            .Where(u => u.Id == userId)
            .Select(u => u.Role!.Rolename)
            .FirstOrDefaultAsync();

        if (string.IsNullOrWhiteSpace(roleName) || !UniversityLevelRoles.Contains(roleName))
        {
            return Results.Forbid();
        }

        return await next(context);
    }

    private static async ValueTask<object?> EnforceAdminOnlyAsync(
        EndpointFilterInvocationContext context,
        EndpointFilterDelegate next)
    {
        var http = context.HttpContext;
        if (http.User?.Identity?.IsAuthenticated != true)
        {
            return Results.Unauthorized();
        }

        var sub = http.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
        if (!Guid.TryParse(sub, out Guid userId))
        {
            return Results.Unauthorized();
        }

        var db = http.RequestServices.GetRequiredService<ServerDbContext>();
        var roleName = await db.Users
            .Where(u => u.Id == userId)
            .Select(u => u.Role!.Rolename)
            .FirstOrDefaultAsync();

        if (!string.Equals(roleName, "관리자", StringComparison.OrdinalIgnoreCase))
        {
            return Results.Forbid();
        }

        return await next(context);
    }

    static private CookieOptions BuildAuthCookieOptions(IConfiguration config)
    {
        bool secure = config.GetValue<bool?>("AuthCookie:Secure")
            ?? config.GetValue<bool?>("AUTH_COOKIE_SECURE")
            ?? true;

        string sameSiteRaw =
            config["AuthCookie:SameSite"]
            ?? config["AUTH_COOKIE_SAMESITE"]
            ?? "None";

        SameSiteMode sameSite = sameSiteRaw.ToLowerInvariant() switch
        {
            "strict" => SameSiteMode.Strict,
            "lax" => SameSiteMode.Lax,
            "none" => SameSiteMode.None,
            _ => SameSiteMode.None,
        };

        return new CookieOptions
        {
            HttpOnly = true,
            Secure = secure,
            SameSite = sameSite,
            IsEssential = true,
            Expires = DateTime.UtcNow.AddMinutes(60),
            Path = "/"
        };
    }

    static public void AddAuthApis(this WebApplication application)
    {
        // 무차별 대입/열거 방어: /auth 전체에 IP 단위 레이트 리밋 적용 (Program.cs "auth" 정책)
        var app = application.MapGroup("/auth").RequireRateLimiting("auth");

        // 유저 이름 조회
        app.MapGet("/name", async (HttpContext context, ServerDbContext db) =>
        {
            string userIdStr = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub)!;
            if (!Guid.TryParse(userIdStr, out Guid userId)) 
            {
                // Fallback to claims if DB fetch isn't desired or fails (though here we want DB)
                return Results.Unauthorized();
            }

            var user = await db.Users.Include(u => u.Major).Include(u => u.Role).FirstOrDefaultAsync(u => u.Id == userId);
            if (user == null) return Results.Unauthorized();

            string name = user.Username;
            // 역할은 JWT 클레임이 아닌 DB에서 읽는다 — 토큰 만료 전이라도 역할 변경(강등 등)이 즉시 반영되도록.
            string roleName = user.Role?.Rolename ?? context.User.FindFirstValue("Role") ?? "Unknown";
            string majorName = user.Major?.Majorname ?? "Unknown"; // Safe navigation

            return Results.Ok(new { Name = name, RoleName = roleName, MajorName = majorName });
        }).RequireAuthorization();

        // 아이디 중복 검사
        app.MapPost("/idcheck", async (ServerDbContext db, IdCheck id) 
            => Results.Ok(
                await db.Users.AnyAsync(u => u.UserId == id.Id)
                || await db.CouncilSignupRequests.AnyAsync(r => r.UserId == id.Id && r.Status == "pending")
            ));

        // 회원가입
        app.MapPost("/signup",
            async (ServerDbContext db, SignupUserDto signup, IValidator<SignupUserDto> validator,
            IMapper mapper) =>
        {
            if (signup.HasForbiddenRoleInput())
            {
                return Results.BadRequest("회원가입 요청에서 역할을 지정할 수 없습니다.");
            }

            var results = validator.Validate(signup);

            if (!results.IsValid) return Results.ValidationProblem(results.ToDictionary());

            if (await db.Users.AnyAsync(p => p.UserId == signup.UserId)) return Results.Conflict("중복된 id");

            // 클라이언트가 보낸 전공이 실제로 존재하는지 검증한다.
            if (!await db.Majors.AnyAsync(m => m.Id == signup.MajorId))
            {
                return Results.BadRequest("유효하지 않은 전공입니다.");
            }

            // 권한 상승 방지: 신규 가입자는 항상 일반학생 역할로 강제한다(클라이언트 RoleId 무시).
            var defaultRole = await db.Roles.FirstOrDefaultAsync(r => r.Rolename == "일반학생");
            if (defaultRole == null)
            {
                return Results.Problem("기본 역할이 구성되지 않았습니다.", statusCode: 500);
            }

            User user = mapper.Map<User>(signup);
            user.RoleId = defaultRole.Id;

            user.HashPassword = BCrypt.Net.BCrypt.HashPassword(signup.Password);
            user.UpdatedTime = user.CreatedTime;

            await db.Users.AddAsync(user);

            await db.SaveChangesAsync();

            return Results.Ok(true);
        });

        app.MapPost("/council-signup-requests",
            async (ServerDbContext db, SignupUserDto signup, IValidator<SignupUserDto> validator) =>
        {
            if (signup.HasForbiddenRoleInput())
            {
                return Results.BadRequest(new { message = "회원가입 요청에서 역할을 지정할 수 없습니다." });
            }

            var results = validator.Validate(signup);
            if (!results.IsValid) return Results.ValidationProblem(results.ToDictionary());

            if (await db.Users.AnyAsync(p => p.UserId == signup.UserId))
            {
                return Results.Conflict(new { message = "이미 사용 중인 아이디입니다." });
            }

            if (await db.CouncilSignupRequests.AnyAsync(r => r.UserId == signup.UserId && r.Status == "pending"))
            {
                return Results.Conflict(new { message = "이미 대기 중인 학생회 가입 요청이 있습니다." });
            }

            if (!await db.Majors.AnyAsync(m => m.Id == signup.MajorId))
            {
                return Results.BadRequest(new { message = "유효하지 않은 전공입니다." });
            }

            CouncilSignupRequest request = new()
            {
                UserId = signup.UserId,
                Username = signup.Username,
                MajorId = signup.MajorId,
                HashPassword = BCrypt.Net.BCrypt.HashPassword(signup.Password),
                Status = "pending",
            };

            await db.CouncilSignupRequests.AddAsync(request);
            await db.SaveChangesAsync();

            return Results.Ok(new
            {
                message = "학생회 가입 요청이 접수되었습니다. 확인을 위해 동똑이 인스타그램으로 DM을 보내주세요.",
                instagramUrl = CouncilSignupInstagramUrl,
            });
        });

        var councilSignupAdmin = app.MapGroup("/council-signup-requests")
            .RequireAuthorization();
        councilSignupAdmin.AddEndpointFilter(EnforceUniversityLevelAsync);

        councilSignupAdmin.MapGet("", async (ServerDbContext db) =>
        {
            var requests = await db.CouncilSignupRequests
                .Include(r => r.Major)
                .OrderByDescending(r => r.CreatedTime)
                .Select(r => new CouncilSignupRequestDto
                {
                    Id = r.Id,
                    UserId = r.UserId,
                    Username = r.Username,
                    MajorId = r.MajorId,
                    MajorName = r.Major == null ? null : r.Major.Majorname,
                    Status = r.Status,
                    CreatedTime = r.CreatedTime,
                    ReviewedTime = r.ReviewedTime,
                    ReviewNote = r.ReviewNote,
                })
                .ToListAsync();

            return Results.Ok(requests);
        });

        councilSignupAdmin.MapPost("/{requestId:guid}/approve", async (
            Guid requestId,
            ServerDbContext db,
            HttpContext context,
            CouncilSignupReviewDto review) =>
        {
            var request = await db.CouncilSignupRequests.FirstOrDefaultAsync(r => r.Id == requestId);
            if (request == null) return Results.NotFound(new { message = "요청을 찾을 수 없습니다." });
            if (request.Status != "pending") return Results.Conflict(new { message = "이미 처리된 요청입니다." });

            if (await db.Users.AnyAsync(u => u.UserId == request.UserId))
            {
                request.Status = "rejected";
                request.ReviewedTime = DateTime.UtcNow;
                request.ReviewNote = "이미 같은 아이디의 계정이 존재합니다.";
                await db.SaveChangesAsync();
                return Results.Conflict(new { message = request.ReviewNote });
            }

            var councilRole = await db.Roles.FirstOrDefaultAsync(r => r.Rolename == "학생회");
            if (councilRole == null)
            {
                return Results.Problem("학생회 역할이 구성되지 않았습니다.", statusCode: 500);
            }

            var reviewerSub = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
            Guid.TryParse(reviewerSub, out Guid reviewerId);

            User user = new()
            {
                UserId = request.UserId,
                HashPassword = request.HashPassword,
                Username = request.Username,
                MajorId = request.MajorId,
                RoleId = councilRole.Id,
                UpdatedTime = DateTime.UtcNow,
            };

            request.Status = "approved";
            request.ReviewedTime = DateTime.UtcNow;
            request.ReviewedByUserId = reviewerId == Guid.Empty ? null : reviewerId;
            request.ReviewNote = review.Note;

            await db.Users.AddAsync(user);
            await db.SaveChangesAsync();

            return Results.Ok(new { message = "학생회 계정이 승인되었습니다." });
        });

        councilSignupAdmin.MapPost("/{requestId:guid}/reject", async (
            Guid requestId,
            ServerDbContext db,
            HttpContext context,
            CouncilSignupReviewDto review) =>
        {
            var request = await db.CouncilSignupRequests.FirstOrDefaultAsync(r => r.Id == requestId);
            if (request == null) return Results.NotFound(new { message = "요청을 찾을 수 없습니다." });
            if (request.Status != "pending") return Results.Conflict(new { message = "이미 처리된 요청입니다." });

            var reviewerSub = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
            Guid.TryParse(reviewerSub, out Guid reviewerId);

            request.Status = "rejected";
            request.ReviewedTime = DateTime.UtcNow;
            request.ReviewedByUserId = reviewerId == Guid.Empty ? null : reviewerId;
            request.ReviewNote = review.Note;

            await db.SaveChangesAsync();
            return Results.Ok(new { message = "학생회 가입 요청이 거절되었습니다." });
        });

        var userAdmin = app.MapGroup("/admin/users")
            .RequireAuthorization();
        userAdmin.AddEndpointFilter(EnforceAdminOnlyAsync);

        userAdmin.MapGet("", async (ServerDbContext db) =>
        {
            var users = await db.Users
                .Include(u => u.Major)
                .Include(u => u.Role)
                .OrderBy(u => u.Role == null ? "" : u.Role.Rolename)
                .ThenBy(u => u.UserId)
                .Select(u => new AdminUserDto
                {
                    Id = u.Id,
                    UserId = u.UserId,
                    Username = u.Username,
                    MajorId = u.MajorId,
                    MajorName = u.Major == null ? null : u.Major.Majorname,
                    RoleId = u.RoleId,
                    RoleName = u.Role == null ? null : u.Role.Rolename,
                    CreatedTime = u.CreatedTime,
                    UpdatedTime = u.UpdatedTime,
                })
                .ToListAsync();

            return Results.Ok(users);
        });

        userAdmin.MapGet("/roles", async (ServerDbContext db) =>
        {
            var roles = await db.Roles
                .OrderBy(r => r.Rolename)
                .Select(r => new AdminRoleDto
                {
                    Id = r.Id,
                    RoleName = r.Rolename,
                })
                .ToListAsync();

            return Results.Ok(roles);
        });

        userAdmin.MapPatch("/{targetUserId:guid}", async (
            Guid targetUserId,
            ServerDbContext db,
            HttpContext context,
            AdminUserUpdateDto update) =>
        {
            var user = await db.Users.FirstOrDefaultAsync(u => u.Id == targetUserId);
            if (user == null) return Results.NotFound(new { message = "계정을 찾을 수 없습니다." });

            var hasChange = false;

            if (update.Username != null)
            {
                var trimmedName = update.Username.Trim();
                if (trimmedName.Length < 2 || trimmedName.Length > 10)
                {
                    return Results.BadRequest(new { message = "이름은 2자 이상 10자 이하로 입력해주세요." });
                }

                user.Username = trimmedName;
                hasChange = true;
            }

            if (update.MajorId.HasValue)
            {
                if (!await db.Majors.AnyAsync(m => m.Id == update.MajorId.Value))
                {
                    return Results.BadRequest(new { message = "유효하지 않은 전공입니다." });
                }

                user.MajorId = update.MajorId.Value;
                hasChange = true;
            }

            if (update.RoleId.HasValue)
            {
                if (!await db.Roles.AnyAsync(r => r.Id == update.RoleId.Value))
                {
                    return Results.BadRequest(new { message = "유효하지 않은 역할입니다." });
                }

                var adminRoleId = await db.Roles
                    .Where(r => r.Rolename == "관리자")
                    .Select(r => (Guid?)r.Id)
                    .FirstOrDefaultAsync();

                if (!adminRoleId.HasValue)
                {
                    return Results.Problem("관리자 역할이 구성되지 않았습니다.", statusCode: 500);
                }

                var callerSub = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
                Guid.TryParse(callerSub, out Guid callerId);
                var demotesSelf = callerId == targetUserId && update.RoleId.Value != adminRoleId.Value;
                var demotesAdmin = user.RoleId == adminRoleId.Value && update.RoleId.Value != adminRoleId.Value;

                if (demotesSelf)
                {
                    return Results.Conflict(new { message = "자기 자신의 관리자 권한은 변경할 수 없습니다." });
                }

                if (demotesAdmin)
                {
                    var adminCount = await db.Users.CountAsync(u => u.RoleId == adminRoleId.Value);
                    if (adminCount <= 1)
                    {
                        return Results.Conflict(new { message = "마지막 관리자 계정은 강등할 수 없습니다." });
                    }
                }

                user.RoleId = update.RoleId.Value;
                hasChange = true;
            }

            if (!hasChange)
            {
                return Results.BadRequest(new { message = "변경할 항목이 없습니다." });
            }

            user.UpdatedTime = DateTime.UtcNow;
            await db.SaveChangesAsync();

            return Results.Ok(new { message = "계정 정보가 수정되었습니다." });
        });

        userAdmin.MapPost("/{targetUserId:guid}/reset-password", async (
            Guid targetUserId,
            ServerDbContext db,
            AdminUserPasswordResetDto reset) =>
        {
            var password = reset.Password?.Trim();
            if (string.IsNullOrWhiteSpace(password) || password.Length < 10 || password.Length > 30)
            {
                return Results.BadRequest(new { message = "비밀번호는 10자 이상 30자 이하로 입력해주세요." });
            }

            var user = await db.Users.FirstOrDefaultAsync(u => u.Id == targetUserId);
            if (user == null) return Results.NotFound(new { message = "계정을 찾을 수 없습니다." });

            user.HashPassword = BCrypt.Net.BCrypt.HashPassword(password);
            user.UpdatedTime = DateTime.UtcNow;

            await db.SaveChangesAsync();
            return Results.Ok(new { message = "비밀번호가 재설정되었습니다." });
        });

        userAdmin.MapDelete("/{targetUserId:guid}", async (
            Guid targetUserId,
            ServerDbContext db,
            HttpContext context) =>
        {
            var callerSub = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
            if (!Guid.TryParse(callerSub, out Guid callerId))
            {
                return Results.Unauthorized();
            }

            if (callerId == targetUserId)
            {
                return Results.BadRequest(new { message = "자기 자신의 계정은 삭제할 수 없습니다." });
            }

            var user = await db.Users
                .Include(u => u.Role)
                .FirstOrDefaultAsync(u => u.Id == targetUserId);
            if (user == null) return Results.NotFound(new { message = "계정을 찾을 수 없습니다." });

            var roleName = user.Role?.Rolename;
            if (!string.IsNullOrWhiteSpace(roleName) && UniversityLevelRoles.Contains(roleName))
            {
                return Results.BadRequest(new { message = "관리자 권한 계정은 삭제할 수 없습니다." });
            }

            var chatIds = await db.Chats
                .Where(chat => chat.UserId == targetUserId)
                .Select(chat => chat.Id)
                .ToListAsync();

            if (chatIds.Count > 0)
            {
                db.ChatMessages.RemoveRange(db.ChatMessages.Where(message => chatIds.Contains(message.ChatId)));
                db.Chats.RemoveRange(db.Chats.Where(chat => chatIds.Contains(chat.Id)));
            }

            db.Users.Remove(user);
            await db.SaveChangesAsync();

            return Results.NoContent();
        });

        // 로그인
        app.MapPost("/signin", async (ServerDbContext db, SigninUserDto signin, IValidator<SigninUserDto> validator,
            IConfiguration config, HttpContext context) =>
        {

            var results = validator.Validate(signin);

            if (!results.IsValid)
            {
                return Results.Unauthorized();
            }

            User? user = await db.Users.Include(u => u.Role).Include(u => u.Major).FirstOrDefaultAsync(u => u.UserId == signin.UserId);

            if (user == null || !BCrypt.Net.BCrypt.Verify(signin.Password, user.HashPassword))
            {
                return Results.Unauthorized();
            }

            // Role row가 없는 사용자(데이터 손상)는 NRE 대신 명시적 오류 처리
            if (user.Role == null)
            {
                return Results.Problem("사용자 역할 정보가 없습니다. 관리자에게 문의하세요.", statusCode: 500);
            }

            var jwt = config.GetSection("Jwt");

            SigningCredentials credential = new(new SymmetricSecurityKey(Convert.FromBase64String(jwt["Key"]!)),
                SecurityAlgorithms.HmacSha512);

            Claim[] claims_ =
            {
                new Claim(JwtRegisteredClaimNames.Sub, user.Id.ToString()),
                new Claim(JwtRegisteredClaimNames.Name, user.Username),
                new Claim("Role", user.Role.Rolename),
                new Claim("Major", user.Major?.Majorname ?? "Unknown")
            };

            JwtSecurityToken token = new(
                issuer: jwt["Issuer"],
                audience: jwt["Audience"],
                claims: claims_,
                expires: DateTime.UtcNow.AddMinutes(60),
                signingCredentials: credential);

            CookieOptions copt = BuildAuthCookieOptions(config);

            string tokenString = new JwtSecurityTokenHandler().WriteToken(token);
            context.Response.Cookies.Append("renux-server-token", tokenString, copt);

            return Results.Ok(true);
        });

        // 로그아웃
        // POST: GET 상태 변경은 <img> 등으로 강제 로그아웃되는 CSRF 벡터가 됨.
        // 쿠키 삭제는 발급 시와 동일한 Path/SameSite/Secure 옵션을 전달해야 모든 브라우저에서 확실히 지워진다.
        app.MapPost("/signout", (HttpContext context, IConfiguration config) =>
        {
            context.Response.Cookies.Delete("renux-server-token", BuildAuthCookieOptions(config));

            return Results.Ok(new { Message = "Ok" });
        }).RequireAuthorization();

        // 구버전 프론트 호환용 GET (점진 제거 예정)
        app.MapGet("/signout", (HttpContext context, IConfiguration config) =>
        {
            context.Response.Cookies.Delete("renux-server-token", BuildAuthCookieOptions(config));

            return Results.Ok(new { Message = "Ok" });
        }).RequireAuthorization();

    }
}
