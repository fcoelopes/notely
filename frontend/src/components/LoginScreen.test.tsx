import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginScreen } from "./LoginScreen";

const api = vi.hoisted(() => ({
  login: vi.fn(),
  submitMfaCode: vi.fn(),
  submitRecoveryCode: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      message: string,
    ) {
      super(message);
    }
  },
  ...api,
}));

const account = { id: "user-1", email: "dono@exemplo.com", totp_enabled: true };

function credentials() {
  fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: "dono@exemplo.com" } });
  fireEvent.change(screen.getByLabelText("Senha"), { target: { value: "uma-senha-longa" } });
  fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
}

describe("LoginScreen", () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
  });

  afterEach(() => {
    cleanup();
  });

  it("authenticates directly when the account has no second factor", async () => {
    const onAuthenticated = vi.fn();
    api.login.mockResolvedValue({ stage: "active", mfa_required: false, user: account });
    render(<LoginScreen onAuthenticated={onAuthenticated} />);

    credentials();

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith(account));
    expect(api.login).toHaveBeenCalledWith("dono@exemplo.com", "uma-senha-longa");
  });

  it("asks for the second factor without authenticating yet", async () => {
    const onAuthenticated = vi.fn();
    api.login.mockResolvedValue({ stage: "mfa_pending", mfa_required: true, user: account });
    render(<LoginScreen onAuthenticated={onAuthenticated} />);

    credentials();

    // A sessão pendente não vale: falta o código.
    expect(await screen.findByLabelText("Código")).toBeInTheDocument();
    expect(onAuthenticated).not.toHaveBeenCalled();

    api.submitMfaCode.mockResolvedValue({ stage: "active", mfa_required: false, user: account });
    fireEvent.change(screen.getByLabelText("Código"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith(account));
    expect(api.submitMfaCode).toHaveBeenCalledWith("123456");
  });

  it("keeps the same message for unknown email and wrong password", async () => {
    const { ApiError } = await import("../lib/api");
    api.login.mockRejectedValue(new ApiError(401, "invalid credentials"));
    render(<LoginScreen onAuthenticated={vi.fn()} />);

    credentials();

    expect(await screen.findByRole("alert")).toHaveTextContent("E-mail ou senha inválidos.");
  });

  it("explains the temporary lockout instead of blaming the password", async () => {
    const { ApiError } = await import("../lib/api");
    api.login.mockRejectedValue(new ApiError(429, "too many failed attempts"));
    render(<LoginScreen onAuthenticated={vi.fn()} />);

    credentials();

    expect(await screen.findByRole("alert")).toHaveTextContent("Muitas tentativas");
  });

  it("switches to a recovery code and explains an invalid code", async () => {
    const { ApiError } = await import("../lib/api");
    api.login.mockResolvedValue({ stage: "mfa_pending", mfa_required: true, user: account });
    api.submitRecoveryCode.mockRejectedValue(new ApiError(401, "invalid recovery code"));
    render(<LoginScreen onAuthenticated={vi.fn()} />);

    credentials();
    fireEvent.click(await screen.findByRole("button", { name: "Usar um código de recuperação" }));

    const field = screen.getByLabelText("Código de recuperação");
    fireEvent.change(field, { target: { value: "AAAA-1111" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));

    await waitFor(() => expect(api.submitRecoveryCode).toHaveBeenCalledWith("AAAA-1111"));
    expect(api.submitMfaCode).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("Código inválido ou já utilizado.");
  });

  it("reports a server that cannot verify the second factor", async () => {
    const { ApiError } = await import("../lib/api");
    api.login.mockResolvedValue({ stage: "mfa_pending", mfa_required: true, user: account });
    api.submitMfaCode.mockRejectedValue(new ApiError(503, "server key missing"));
    render(<LoginScreen onAuthenticated={vi.fn()} />);

    credentials();
    fireEvent.change(await screen.findByLabelText("Código"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("não está configurado");
  });
});
