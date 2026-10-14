import { useState } from "react";
import { ApiError, login, submitMfaCode, submitRecoveryCode } from "../lib/api";
import type { Account } from "../types";

interface Props {
  onAuthenticated: (account: Account) => void;
}

type Step = "credentials" | "second-factor";

function credentialsMessage(failure: unknown): string {
  if (failure instanceof ApiError && failure.status === 429) {
    return "Muitas tentativas. Aguarde alguns minutos antes de tentar de novo.";
  }
  // O mesmo texto para e-mail inexistente e senha errada: o servidor não distingue.
  return "E-mail ou senha inválidos.";
}

function secondFactorMessage(failure: unknown): string {
  if (failure instanceof ApiError && failure.status === 503) {
    return "O servidor não está configurado para verificar o segundo fator.";
  }
  return "Código inválido ou já utilizado.";
}

export function LoginScreen({ onAuthenticated }: Props) {
  const [step, setStep] = useState<Step>("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submitCredentials(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const outcome = await login(email, password);
      if (outcome.mfa_required) {
        setStep("second-factor");
        return;
      }
      onAuthenticated(outcome.user);
    } catch (failure) {
      setError(credentialsMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function submitSecondFactor(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const outcome = recovery
        ? await submitRecoveryCode(code)
        : await submitMfaCode(code);
      onAuthenticated(outcome.user);
    } catch (failure) {
      setError(secondFactorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <section className="login-card" aria-labelledby="login-title">
        <h1 id="login-title">Notely</h1>
        {step === "credentials" ? (
          <>
            <p className="login-hint">Entre para acessar as suas notas e o seu acervo.</p>
            <form onSubmit={submitCredentials}>
              <label htmlFor="login-email">E-mail</label>
              <input
                id="login-email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
              <label htmlFor="login-password">Senha</label>
              <input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
              <button type="submit" disabled={busy}>
                {busy ? "Entrando…" : "Entrar"}
              </button>
            </form>
          </>
        ) : (
          <>
            <p className="login-hint">
              {recovery
                ? "Informe um dos códigos de recuperação que você guardou."
                : "Informe o código de seis dígitos do seu aplicativo autenticador."}
            </p>
            <form onSubmit={submitSecondFactor}>
              <label htmlFor="login-code">
                {recovery ? "Código de recuperação" : "Código"}
              </label>
              <input
                id="login-code"
                // O autenticador usa inputMode numérico; o código de recuperação é alfanumérico.
                inputMode={recovery ? "text" : "numeric"}
                autoComplete="one-time-code"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                required
              />
              <button type="submit" disabled={busy}>
                {busy ? "Confirmando…" : "Confirmar"}
              </button>
            </form>
            <button
              type="button"
              className="login-alternate"
              onClick={() => {
                setRecovery((current) => !current);
                setCode("");
                setError(null);
              }}
            >
              {recovery ? "Usar o aplicativo autenticador" : "Usar um código de recuperação"}
            </button>
          </>
        )}
        {error !== null && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
      </section>
    </main>
  );
}
