FROM mcr.microsoft.com/dotnet/sdk:9.0-noble AS build
WORKDIR /src

COPY *.sln .
COPY *.csproj .

RUN dotnet restore "RenuxServer.sln"

COPY . .

RUN dotnet publish -c Release -o /app/publish /p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:9.0-noble AS final
WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/publish .

ENTRYPOINT ["dotnet", "RenuxServer.dll"]