using FluentValidation;

using RenuxServer.Dtos.AuthDtos;

namespace RenuxServer.Validators;

public class SigninUserValidator : AbstractValidator<SigninUserDto>
{
    public SigninUserValidator()
    {
        RuleFor(p => p.UserId)
            .NotNull()
            .Length(8, 30);
        RuleFor(p => p.Password)
            .NotNull()
            .Length(10, 30)
            .Matches(@"[0-9]")
            .Matches(@"[a-z]")
            .Matches(@"[A-Z]")
            .Matches(@"[,./!@#$]");
    }
}
