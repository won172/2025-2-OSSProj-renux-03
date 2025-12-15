namespace RenuxServer.Apis;

static public class AdminProxyApis
{
    static public void AddAdminProxyApis(this WebApplication application)
    {
        var app = application.MapGroup("/admin");
        string RagServiceUrl = application.Configuration["RagServiceUrl"] ?? "http://rag-service:8000";

        app.MapGet("/pending", async (HttpResponse response) => 
        {
            using var client = new HttpClient();
            var proxyRes = await client.GetAsync($"{RagServiceUrl}/admin/pending");
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

        app.MapPost("/submit", async (HttpRequest request, HttpResponse response) => 
        {
            using var client = new HttpClient();
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

        app.MapPost("/approve/{id}", async (int id, HttpResponse response) => 
        {
            using var client = new HttpClient();
            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/approve/{id}", null);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });

         app.MapPost("/reject/{id}", async (int id, HttpResponse response) => 
        {
            using var client = new HttpClient();
            var proxyRes = await client.PostAsync($"{RagServiceUrl}/admin/reject/{id}", null);
            response.StatusCode = (int)proxyRes.StatusCode;
            var contentStream = await proxyRes.Content.ReadAsStreamAsync();
            return Results.Stream(contentStream, contentType: proxyRes.Content.Headers.ContentType?.ToString() ?? "application/json");
        });
    }
}
