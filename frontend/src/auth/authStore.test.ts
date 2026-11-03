import { authStore } from "./authStore";
import { decodeClaims } from "./jwt";

const encode = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const jwt = (claims: object) => `${encode({ alg: "HS256" })}.${encode(claims)}.signature`;
const inOneHour = () => Math.floor(Date.now() / 1000) + 3600;

describe("decodeClaims", () => {
  it("reads the tenant claims of a JWT", () => {
    const token = jwt({ tenant_id: "tenant_a", tenant_name: "Rutaviva Logistics", exp: 1 });
    expect(decodeClaims(token)).toEqual({ tenant_id: "tenant_a", tenant_name: "Rutaviva Logistics", exp: 1 });
  });

  it("rejects text that is not a JWT", () => {
    expect(decodeClaims("hello")).toBeNull();
    expect(decodeClaims("a.%%%.c")).toBeNull();
  });
});

describe("authStore", () => {
  beforeEach(() => {
    authStore.getState().sessions.forEach((s) => authStore.remove(s.tenantKey));
  });

  it("labels a token with tenant_name, falling back to tenant_id", () => {
    authStore.addToken(jwt({ tenant_id: "tenant_a", tenant_name: "Rutaviva Logistics", exp: inOneHour() }));
    authStore.addToken(jwt({ tenant_id: "tenant_b", exp: inOneHour() }));
    expect(authStore.getState().sessions.map((s) => s.label)).toEqual(["Rutaviva Logistics", "tenant_b"]);
    expect(authStore.getState().activeKey).toBe("tenant_b");
  });

  it("replaces the token of a tenant instead of adding a second one", () => {
    authStore.addToken(jwt({ tenant_id: "tenant_a", exp: inOneHour() }));
    const second = jwt({ tenant_id: "tenant_a", exp: inOneHour() + 60 });
    authStore.addToken(second);
    expect(authStore.getState().sessions).toHaveLength(1);
    expect(authStore.getActiveToken()).toBe(second);
  });

  it("refuses expired tokens and tokens without a tenant", () => {
    expect(authStore.addToken(jwt({ tenant_id: "t", exp: 1 }))).toEqual({
      ok: false,
      error: "This token has already expired.",
    });
    expect(authStore.addToken(jwt({ sub: "someone" })).ok).toBe(false);
  });

  it("flags the active token after a 401 and clears the flag on switch", () => {
    authStore.addToken(jwt({ tenant_id: "tenant_a", exp: inOneHour() }));
    const tokenA = authStore.getActiveToken() as string;
    authStore.addToken(jwt({ tenant_id: "tenant_b", exp: inOneHour() }));
    authStore.select("tenant_a");

    authStore.markInvalid(tokenA);
    expect(authStore.getState().activeTokenInvalid).toBe(true);

    authStore.select("tenant_b");
    expect(authStore.getState().activeTokenInvalid).toBe(false);
  });
});
