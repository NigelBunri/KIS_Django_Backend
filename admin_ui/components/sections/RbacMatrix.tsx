"use client";

const roles = ["Super Admin", "App Analyst", "Viewer"];
const permissions = [
  { label: "Dashboard read", scopes: ["super", "analyst", "viewer"] },
  { label: "CRUD write", scopes: ["super"] },
  { label: "Audit review", scopes: ["super", "analyst"] },
  { label: "Monitor alerts", scopes: ["super", "analyst"] }
];

export function RbacMatrix() {
  return (
    <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-6">
      <h2 className="text-xl font-semibold text-white">Permission matrix</h2>
      <div className="mt-6 overflow-auto">
        <table className="w-full text-left text-sm text-slate-300">
          <thead>
            <tr className="text-xs uppercase tracking-widest text-slate-500">
              <th className="px-4 py-3">Permission</th>
              {roles.map((role) => (
                <th key={role} className="px-4 py-3 text-center">
                  {role}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {permissions.map((perm) => (
              <tr key={perm.label} className="border-t border-white/5">
                <td className="px-4 py-4 text-white">{perm.label}</td>
                {roles.map((role) => (
                  <td key={role} className="px-4 py-4 text-center">
                    {perm.scopes.includes(role.toLowerCase().split(" ")[0]) ? (
                      <span className="rounded-full bg-emerald-500/10 px-3 py-1 text-xs uppercase tracking-wider text-emerald-300">
                        Enabled
                      </span>
                    ) : (
                      <span className="rounded-full bg-slate-700 px-3 py-1 text-xs uppercase tracking-wider text-slate-500">
                        Locked
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
