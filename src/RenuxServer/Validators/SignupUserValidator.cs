using FluentValidation;
using RenuxServer.Dtos.AuthDtos;

namespace RenuxServer.Validators;

public class SignupUserValidator : AbstractValidator<SignupUserDto>
{
    public SignupUserValidator()
    {
        RuleFor(p => p.UserId)
            .NotNull().WithMessage("필수 입력")
            .Length(8, 30).WithMessage("8글자 이상 30글자 이하");
        RuleFor(p => p.Password)
            .NotNull().WithMessage("필수 입력")
            .Length(10, 30).WithMessage("10글자 이상 30글자 이하")
            .Matches(@"[0-9]").WithMessage("숫자 포함")
            .Matches(@"[a-z]").WithMessage("소문자 포함")
            .Matches(@"[A-Z]").WithMessage("대문자 포함")
            .Matches(@"[,./!@#$]").WithMessage("지정된 특수문자 포함");
        RuleFor(p => p.Username)
            .NotNull().WithMessage("필수 입력")
            .Length(2, 10).WithMessage("2글자 이상 10글자 이하");
    }
}
