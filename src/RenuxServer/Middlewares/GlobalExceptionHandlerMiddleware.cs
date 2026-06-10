using System.Net;
using System.Text.Json;

namespace RenuxServer.Middlewares;

public class GlobalExceptionHandlerMiddleware
{
    private readonly RequestDelegate _next;
    private readonly ILogger<GlobalExceptionHandlerMiddleware> _logger;
    private readonly IHostEnvironment _environment;

    public GlobalExceptionHandlerMiddleware(
        RequestDelegate next,
        ILogger<GlobalExceptionHandlerMiddleware> logger,
        IHostEnvironment environment)
    {
        _next = next;
        _logger = logger;
        _environment = environment;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        try
        {
            await _next(context);
        }
        catch (Exception e)
        {
            // 상세 정보(연결문자열/경로/스택)는 서버 로그로만 남기고 클라이언트에는 노출하지 않는다.
            _logger.LogError(e, "Unhandled exception. TraceId={TraceId}", context.TraceIdentifier);
            await HandleException(context, e);
        }
    }

    private Task HandleException(HttpContext context, Exception ex)
    {
        context.Response.ContentType = "application/json";
        context.Response.StatusCode = (int)HttpStatusCode.InternalServerError;

        var response = new
        {
            context.Response.StatusCode,
            Message = "서버 오류 관리자 문의",
            // 운영 환경에서는 예외 메시지를 숨긴다. 개발 환경에서만 디버깅용으로 노출.
            Detail = _environment.IsDevelopment() ? ex.Message : null
        };

        string resString = JsonSerializer.Serialize(response);

        return context.Response.WriteAsync(resString);
    }
}
