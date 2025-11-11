using AutoMapper;
using Microsoft.EntityFrameworkCore;
using FluentValidation;

using RenuxServer.DbContexts;
using RenuxServer.Dtos.AuthDtos;
using RenuxServer.Middlewares;
using RenuxServer.Validators;
using RenuxServer.Apis.Auth;

using Microsoft.IdentityModel.Tokens;
using RenuxServer.Models;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Dtos.EtcDtos;
using RenuxServer.Apis;
using RenuxServer.Apis.Chat;
using System.Text;

var builder = WebApplication.CreateBuilder();

builder.Configuration.AddUserSecrets<Program>();

// DbContext Setting
builder.Services.AddDbContext<ServerDbContext>(options =>
{
    options.UseNpgsql(builder.Configuration.GetConnectionString("RenuxServer"));
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
builder.Services.AddAuthentication()
    .AddJwtBearer(options =>
    {
        options.TokenValidationParameters = new()
        {
            IssuerSigningKey = new SymmetricSecurityKey(Convert.FromBase64String(builder.Configuration["Jwt:Key"]!)),
            ValidateIssuer = false,
            ValidateAudience = false
        };

        options.Events = new()
        {
            OnMessageReceived = new(context =>
            {
                if (context.Request.Cookies.ContainsKey("renux-server-token"))
                    context.Token = context.Request.Cookies["renux-server-token"];
                return Task.CompletedTask;
            })
        };
    });

builder.Services.AddAuthorization();

var app = builder.Build();

app.UseMiddleware<GlobalExceptionHandlerMiddleware>();

using (var db = app.Services.GetService<ServerDbContext>()!)
{
    try
    {
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

app.MapGet("/", async (HttpContext context) =>
{
    context.Response.ContentType = "text/html";
    await context.Response.SendFileAsync("wwwroot/index.html");
});

app.Run();