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
            => Results.Ok(await db.Users.AnyAsync(u => u.UserId==id.Id)));

        // 회원가입
        app.MapPost("/signup",
            async (ServerDbContext db, SignupUserDto signup, IValidator<SignupUserDto> validator,
            IMapper mapper) =>
        {
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
