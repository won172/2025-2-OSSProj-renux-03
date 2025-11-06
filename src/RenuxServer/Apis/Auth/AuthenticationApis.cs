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

        app.MapGet("/name", (HttpContext context) =>
        {
            string name = context.User.FindFirstValue(JwtRegisteredClaimNames.Name)!;
            return Results.Ok(new { Name = name });
        }).RequireAuthorization();

        app.MapGet("/up", async (HttpContext context) =>
        {
            context.Response.ContentType = "text/html";
            await context.Response.SendFileAsync("wwwroot/signup.html");
        });

        app.MapGet("/in", async (HttpContext context) =>
        {
            context.Response.ContentType = "text/html";
            await context.Response.SendFileAsync("wwwroot/signin.html");
        });

        app.MapPost("/idcheck", async (ServerDbContext db, IdCheck id) 
            => Results.Ok(await db.Users.AnyAsync(u => u.UserId==id.Id)));

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

            return Results.Ok(new { Message = "OK" });
        });

        app.MapPost("/signin", async (ServerDbContext db, SigninUserDto signin, IValidator<SigninUserDto> validator,
            IConfiguration config, HttpContext context) =>
        {

            var results = validator.Validate(signin);

            if (!results.IsValid)
            {
                return Results.Unauthorized();
            }

            User? user = await db.Users.FirstOrDefaultAsync(u => u.UserId == signin.UserId);

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
                new Claim(JwtRegisteredClaimNames.Name, user.Username)
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
                //Secure = true,
                Expires = DateTime.Now.AddMinutes(60)
            };

            string tokenString = new JwtSecurityTokenHandler().WriteToken(token);
            context.Response.Cookies.Append("renux-server-token", tokenString, copt);

            return Results.Ok(new { Message = "OK" });
        });

        app.MapGet("/signout", (HttpContext context) =>
        {
            context.Response.Cookies.Delete("renux-server-token");

            return Results.Ok(new { Message = "Ok" });
        }).RequireAuthorization();

    }
}
