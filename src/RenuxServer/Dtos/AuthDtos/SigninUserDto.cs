namespace RenuxServer.Dtos.AuthDtos;

public record SigninUserDto(string UserId, string Password);

public record DeleteAccountDto(string Password, string Confirmation);
