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
    static public void AddAuthApis(this WebApplication application)
    {
        var app = application.MapGroup("/auth");

        // 유저 이름 조회
        app.MapGet("/name", async (HttpContext context, ServerDbContext db) =>
        {
            string userIdStr = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub)!;
            if (!Guid.TryParse(userIdStr, out Guid userId)) 
            {
                // Fallback to claims if DB fetch isn't desired or fails (though here we want DB)
                return Results.Unauthorized();
            }

            var user = await db.Users.Include(u => u.Major).FirstOrDefaultAsync(u => u.Id == userId);
            if (user == null) return Results.Unauthorized();

            string name = user.Username;
            string roleName = context.User.FindFirstValue("Role")!;
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

            User user = mapper.Map<User>(signup);

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

            var jwt = config.GetSection("Jwt");

            SigningCredentials credential = new(new SymmetricSecurityKey(Convert.FromBase64String(jwt["Key"]!)),
                SecurityAlgorithms.HmacSha512);

            Claim[] claims_ =
            {
                new Claim(JwtRegisteredClaimNames.Sub, user.Id.ToString()),
                new Claim(JwtRegisteredClaimNames.Name, user.Username),
                new Claim("Role", user.Role!.Rolename),
                new Claim("Major", user.Major?.Majorname ?? "Unknown")
            };

            JwtSecurityToken token = new(
                issuer: jwt["Issuer"],
                audience: jwt["Audience"],
                claims: claims_,
                expires: DateTime.Now.AddMinutes(60),
                signingCredentials: credential);

            CookieOptions copt = new()
            {
                HttpOnly = true,
                Secure = false, // Set to true in production with HTTPS
                SameSite = SameSiteMode.Lax,
                IsEssential = true,
                Expires = DateTime.UtcNow.AddMinutes(60),
                Path = "/"
            };

            string tokenString = new JwtSecurityTokenHandler().WriteToken(token);
            context.Response.Cookies.Append("renux-server-token", tokenString, copt);

            return Results.Ok(true);
        });

        // 로그아웃
        app.MapGet("/signout", (HttpContext context) =>
        {
            context.Response.Cookies.Delete("renux-server-token");

            return Results.Ok(new { Message = "Ok" });
        }).RequireAuthorization();

    }
}
