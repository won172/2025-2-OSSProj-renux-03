namespace RenuxServer.Apis;

static public class AdminProxyApis
{
    private static readonly HashSet<string> AllowedAdminRoles = new(StringComparer.OrdinalIgnoreCase)
    {
        "관리자",
        "학생회",
        "총학생회",
    };

    static public void AddAdminProxyApis(this WebApplication application)
    {
        var app = application.MapGroup("/admin").RequireAuthorization();
        string RagServiceUrl = application.Configuration["RagServiceUrl"] ?? application.Configuration["RAG_SERVICE_URL"] ?? "http://rag-service:8000";

        app.AddEndpointFilter(async (context, next) =>
        {
            var user = context.HttpContext.User;
            if (user?.Identity?.IsAuthenticated != true)
            {
                return Results.Unauthorized();
            }

            var roleName = user.FindFirst("Role")?.Value;
            if (string.IsNullOrWhiteSpace(roleName) || !AllowedAdminRoles.Contains(roleName))
            {
                return Results.Forbid();
            }

            return await next(context);
        });

        app.MapGet("pending", async (HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) => 
        {
            logger.LogInformation("Proxying /admin/pending to {Url}/admin/pending", RagServiceUrl);
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/pending");
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

        app.MapGet("items", async (HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) => 
        {
            logger.LogInformation("Proxying /admin/items to {Url}/admin/items", RagServiceUrl);
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/items");
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

        app.MapGet("rag/status", async (HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) =>
        {
            logger.LogInformation("Proxying /admin/rag/status to {Url}/admin/rag/status", RagServiceUrl);
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/rag/status");
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

        app.MapGet("rag-logs-list", async (HttpRequest request, HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) =>
        {
            string url = $"{RagServiceUrl}/admin/rag/logs{request.QueryString}";
            logger.LogInformation("Proxying /admin/rag-logs-list to {Url}", url);
            try 
            {
                var client = httpClientFactory.CreateClient();
                var proxyRes = await client.GetAsync(url);
                logger.LogInformation("RAG service response: {Status}", proxyRes.StatusCode);
                response.StatusCode = (int)proxyRes.StatusCode;
                var contentStream = await proxyRes.Content.ReadAsStreamAsync();
                return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Error proxying to RAG service at {Url}", url);
                return Results.Problem(detail: ex.Message, statusCode: 500);
            }
        });

        app.MapGet("rag-logs/export", async (HttpRequest request, HttpResponse response, IHttpClientFactory httpClientFactory, ILogger<Program> logger) =>
        {
            logger.LogInformation("Proxying /admin/rag-logs/export to {Url}/admin/rag-logs/export", RagServiceUrl);
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/rag-logs/export{request.QueryString}");
            response.StatusCode = (int)proxyRes.StatusCode;
            if (proxyRes.Content.Headers.ContentDisposition != null)
            {
                response.Headers.ContentDisposition = proxyRes.Content.Headers.ContentDisposition.ToString();
            }
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "text/csv");
        });

        app.MapPost("/submit", async (HttpRequest request, HttpResponse response, IHttpClientFactory httpClientFactory) =>
        {
            var client = httpClientFactory.CreateClient();
            using var streamContent = new StreamContent(request.Body);
            if (request.ContentType != null)
            {
                streamContent.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(request.ContentType);
            }
            
            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/submit", streamContent);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

        app.MapPost("/approve/{id}", async (int id, HttpResponse response, IHttpClientFactory httpClientFactory) =>
        {
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/approve/{id}", null);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

         app.MapPost("/reject/{id}", async (int id, HttpResponse response, IHttpClientFactory httpClientFactory) =>
        {
            var client = httpClientFactory.CreateClient();
            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/reject/{id}", null);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });
    }
}
