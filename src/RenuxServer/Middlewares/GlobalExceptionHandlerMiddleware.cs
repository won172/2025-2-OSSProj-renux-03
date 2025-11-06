using System.Net;
using System.Text.Json;

namespace RenuxServer.Middlewares;

public class GlobalExceptionHandlerMiddleware
{
    private readonly RequestDelegate _next;

    public GlobalExceptionHandlerMiddleware(RequestDelegate next)
    {
        _next = next;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        try
        {
            await _next(context);
        }
        catch(Exception e)
        {
            Console.WriteLine(e.GetType());
            Console.WriteLine(e.Message);
            await HandleException(context, e);
        }
    }

    private static Task HandleException(HttpContext context, Exception ex)
    {
        context.Response.ContentType = "text/json";
        context.Response.StatusCode = (int)HttpStatusCode.InternalServerError;

        var response = new
        {
            context.Response.StatusCode,
            Message = "서버 오류 관리자 문의",
            Detail = ex.Message
        };

        string resString = JsonSerializer.Serialize(response);

        return context.Response.WriteAsync(resString);
    }
}
