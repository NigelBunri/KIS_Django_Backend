"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  performBulkAction,
  updateModelInstance,
} from "@/lib/api";
import { Skeleton } from "@/components/ui/Skeleton";
import { useModelData } from "@/hooks/useModelData";
import { useModelRegistry } from "@/hooks/useModelRegistry";

const BULK_ACTIONS = ["soft_delete", "restore", "hard_delete"] as const;

type BulkActionType = (typeof BULK_ACTIONS)[number];

export function CrudEngine() {
  const registryQuery = useModelRegistry();
  const registry = registryQuery.data ?? [];
  const [selectedApp, setSelectedApp] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [search, setSearch] = useState("");
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [ordering, setOrdering] = useState("");
  const [perPage, setPerPage] = useState(25);
  const [filterField, setFilterField] = useState("");
  const [filterValue, setFilterValue] = useState("");
  const [activeFilters, setActiveFilters] = useState<Record<string, string>>({});
  const [visibleFields, setVisibleFields] = useState<string[]>([]);
  const [rowSelection, setRowSelection] = useState({});
  const [bulkAction, setBulkAction] = useState<BulkActionType>("soft_delete");
  const [submittingBulk, setSubmittingBulk] = useState(false);
  const [inlineBusy, setInlineBusy] = useState(false);
  const [notification, setNotification] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const filterKey = useMemo(() => JSON.stringify(activeFilters), [activeFilters]);

  useEffect(() => {
    if (!selectedApp && Array.isArray(registry) && registry.length) {
      setSelectedApp(registry[0].app_label);
    }
  }, [registry, selectedApp]);

  useEffect(() => {
    if (!selectedApp) {
      return;
    }
    const models = registry.find((entry) => entry.app_label === selectedApp)?.models ?? [];
    if (!selectedModel && models.length) {
      setSelectedModel(models[0]);
    }
  }, [selectedApp, registry, selectedModel]);

  const modelParams = useMemo(() => {
    return {
      search,
      ordering,
      include_deleted: includeDeleted,
      per_page: perPage,
      page,
      filters: activeFilters,
    };
  }, [search, ordering, includeDeleted, perPage, activeFilters, page]);

  const modelQuery = useModelData(
    selectedApp,
    selectedModel,
    modelParams,
    Boolean(selectedApp && selectedModel)
  );

  const fieldNames = modelQuery.data?.fields ?? [];
  const metaFields = modelQuery.data?.fields_meta ?? [];
  const metadataMap = useMemo(() => {
    const map: Record<string, typeof metaFields[number]> = {};
    metaFields.forEach((meta) => {
      map[meta.name] = meta;
    });
    return map;
  }, [metaFields]);

  useEffect(() => {
    if (fieldNames.length) {
      const defaultFields = fieldNames.slice(0, 6);
      setVisibleFields(defaultFields);
    }
  }, [fieldNames, selectedModel]);

  useEffect(() => {
    setPage(1);
  }, [selectedApp, selectedModel, search, includeDeleted, ordering, perPage, filterKey]);

  const primaryField = useMemo(() => {
    const pkField = metaFields.find((meta) => meta.primary_key)?.name;
    return pkField ?? fieldNames[0] ?? "id";
  }, [metaFields, fieldNames]);

  const tableData = modelQuery.data?.data ?? [];
  const columns = useMemo<ColumnDef<Record<string, unknown>>[]>(() => {
    const dynamicColumns: ColumnDef<Record<string, unknown>>[] = visibleFields.map((field) => ({
      accessorKey: field,
      header: field,
      cell: ({ row, column }) => {
        const value = row.getValue(column.id as string);
        const metadata = metadataMap[field];
        const displayValue = value === null || value === undefined ? "—" : String(value);
        const readOnly = metadata?.read_only ?? false;
        const isPrimary = metadata?.primary_key ?? false;
        return (
          <div
            className={`text-sm ${readOnly || isPrimary ? "text-slate-400" : "text-white"} cursor-pointer`} 
            onClick={() => {
              if (readOnly || !selectedApp || !selectedModel) {
                return;
              }
              const currentPk = row.original[primaryField] ?? row.original.id;
              if (!currentPk) {
                return;
              }
              const newValue = window.prompt(`Update ${field}`, displayValue);
              if (newValue === null || newValue === displayValue) {
                return;
              }
              setInlineBusy(true);
              updateModelInstance(selectedApp, selectedModel, currentPk, { [field]: newValue })
                .then(() => {
                  modelQuery.refetch();
                  setNotification(`Updated ${field} for ${currentPk}`);
                })
                .catch(() => setNotification("Unable to update field"))
                .finally(() => setInlineBusy(false));
            }}
          >
            {displayValue}
          </div>
        );
      },
    }));

    return [
      {
        id: "select",
        header: ({ table }) => (
          <input
            type="checkbox"
            checked={table.getIsAllRowsSelected()}
            onChange={table.getToggleAllRowsSelectedHandler()}
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={row.getIsSelected()}
            onChange={row.getToggleSelectedHandler()}
          />
        ),
      },
      ...dynamicColumns,
    ];
  }, [visibleFields, metadataMap, selectedApp, selectedModel, modelQuery, primaryField]);

  const table = useReactTable({
    data: tableData,
    columns,
    state: {
      rowSelection,
    },
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
    getRowId: (row) => String(row[primaryField] ?? row.id ?? JSON.stringify(row)),
    getCoreRowModel: getCoreRowModel(),
  });

  const tableContainerRef = useRef<HTMLDivElement>(null);
  const rowVirtualizer = useVirtualizer({
    count: table.getRowModel().rows.length,
    getScrollElement: () => tableContainerRef.current,
    estimateSize: () => 58,
    overscan: 5,
  });
  const virtualRows = rowVirtualizer.getVirtualItems();

  useEffect(() => {
    rowVirtualizer.measure();
  }, [rowVirtualizer, table.getRowModel().rows.length]);

  const selectedRows = table.getSelectedRowModel().flatRows;

  const handleBulkAction = async () => {
    if (!selectedApp || !selectedModel || !selectedRows.length) {
      return;
    }
    const ids = selectedRows
      .map((row) => row.original[primaryField] ?? row.original.id)
      .filter(Boolean);
    if (!ids.length) {
      return;
    }
    if (!window.confirm(`Apply ${bulkAction} to ${ids.length} records?`)) {
      return;
    }
    try {
      setSubmittingBulk(true);
      await performBulkAction(selectedApp, selectedModel, bulkAction, ids);
      await modelQuery.refetch();
      setRowSelection({});
      setNotification(`Bulk ${bulkAction.replace("_", " ")} completed`);
    } catch (error) {
      setNotification("Bulk action failed");
    } finally {
      setSubmittingBulk(false);
    }
  };

  const handleFilterAdd = () => {
    if (!filterField || !filterValue) {
      return;
    }
    setActiveFilters((prev) => ({ ...prev, [filterField]: filterValue }));
    setFilterValue("");
  };

  const removeFilter = (key: string) => {
    setActiveFilters((prev) => {
      const clone = { ...prev };
      delete clone[key];
      return clone;
    });
  };

  const exportToCSV = () => {
    if (!tableData.length) {
      return;
    }
    const headers = fieldNames;
    const rows = tableData.map((row) =>
      headers
        .map((field) => JSON.stringify(row[field] ?? ""))
        .join(",")
    );
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selectedApp}-${selectedModel}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const pagination = modelQuery.data?.pagination;
  const isRegistryLoading = registryQuery.isLoading;
  const registryAvailable = registry.length > 0;
  const isCrudReady = Boolean(selectedApp && selectedModel && registryAvailable);
  const showNoModels = !isRegistryLoading && !registryAvailable;
  const registryNotice = showNoModels
    ? "Waiting for the Django registry endpoint to return models. Ensure /registry/models/ is reachable."
    : !isCrudReady
    ? "Select an app and model to inspect their records."
    : null;

  return (
    <div className="glass-card rounded-3xl border border-white/5 bg-slate-900/70 p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-slate-500">
            Dynamic CRUD explorer
          </p>
          <h2 className="text-2xl font-semibold text-white">
            {selectedApp ? `${selectedApp}.${selectedModel}` : "Choose a model"}
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
            <select
              value={selectedApp}
              onChange={(event) => setSelectedApp(event.target.value)}
              disabled={showNoModels}
              className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:border-slate-700 disabled:text-slate-500"
            >
              {registry.map((entry) => (
                <option key={entry.app_label} value={entry.app_label}>
                  {entry.app_label}
                </option>
              ))}
            </select>
            <select
              value={selectedModel}
              onChange={(event) => setSelectedModel(event.target.value)}
              disabled={!selectedApp || showNoModels}
              className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
            >
              {registry
                .find((entry) => entry.app_label === selectedApp)
                ?.models.map((model) => (
                  <option key={model} value={model}>
                  {model}
                </option>
              ))}
          </select>
          <button
            onClick={exportToCSV}
            className="rounded-2xl border border-indigo-500/40 px-4 py-2 text-xs uppercase tracking-widest text-indigo-300"
          >
            Export CSV
          </button>
        </div>
      </div>

      <div className="mt-5 grid gap-3 rounded-2xl border border-white/5 bg-slate-950/60 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search across fields"
            className="flex-1 rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
          <input
            type="text"
            value={filterValue}
            onChange={(event) => setFilterValue(event.target.value)}
            placeholder="Filter value"
            className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
          />
          <select
            value={filterField}
            onChange={(event) => setFilterField(event.target.value)}
            className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
          >
            <option value="">Select field</option>
            {fieldNames.map((field) => (
              <option key={field} value={field}>
                {field}
              </option>
            ))}
          </select>
          <button
            onClick={handleFilterAdd}
            className="rounded-2xl border border-emerald-400/40 px-4 py-2 text-xs uppercase tracking-widest text-emerald-300"
          >
            Add filter
          </button>
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(event) => setIncludeDeleted(event.target.checked)}
            />
            Include deleted
          </label>
          <input
            type="text"
            value={ordering}
            onChange={(event) => setOrdering(event.target.value)}
            placeholder="ordering (e.g. -created_at)"
            className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
          />
          <select
            value={perPage}
            onChange={(event) => setPerPage(Number(event.target.value))}
            className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-sm text-white"
          >
            {[10, 25, 50, 100].map((size) => (
              <option key={size} value={size}>
                {size} / page
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(activeFilters).map(([key, value]) => (
            <span
              key={key}
              className="flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900/70 px-3 py-1 text-xs uppercase tracking-widest text-slate-300"
            >
              {key}={value}
              <button onClick={() => removeFilter(key)} className="text-rose-400">
                ×
              </button>
            </span>
          ))}
          {Object.keys(activeFilters).length === 0 && (
            <p className="text-xs text-slate-500">No filters applied</p>
          )}
        </div>
        <div className="flex flex-wrap gap-3">
          {fieldNames.map((field) => (
            <button
              key={field}
              onClick={() => {
                setVisibleFields((prev) =>
                  prev.includes(field)
                    ? prev.filter((item) => item !== field)
                    : [...prev, field]
                );
              }}
              className={`rounded-full border px-3 py-1 text-xs tracking-widest transition ${visibleFields.includes(field)
                ? "border-indigo-500/40 bg-indigo-500/20 text-white"
                : "border-slate-800 text-slate-400"}
            }`}
            >
              {field}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-slate-300">
          {pagination && (
            <span>
              Page {pagination.page} / {pagination.total_pages} ({pagination.total_items} items)
            </span>
          )}
          {notification && (
            <span className="text-emerald-300">{notification}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={bulkAction}
            onChange={(event) => setBulkAction(event.target.value as BulkActionType)}
            className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-2 text-xs text-white"
          >
            {BULK_ACTIONS.map((action) => (
              <option key={action} value={action}>
                {action.replace("_", " ")}
              </option>
            ))}
          </select>
          <button
            onClick={handleBulkAction}
            disabled={submittingBulk || inlineBusy || !selectedRows.length}
            className="rounded-2xl border border-white/20 bg-gradient-to-r from-indigo-500 to-sky-500 px-4 py-2 text-xs font-semibold uppercase tracking-widest text-white disabled:opacity-40"
          >
            {submittingBulk ? "Applying…" : "Apply bulk action"}
          </button>
        </div>
      </div>

      <div className="mt-5 overflow-hidden rounded-3xl border border-white/5 bg-slate-950/70">
        <div
          ref={tableContainerRef}
          className="min-h-[320px] max-h-[460px] overflow-auto"
        >
          {!isCrudReady ? (
            <div className="flex h-full min-h-[320px] items-center justify-center">
              <p className="text-sm text-slate-400 text-center px-4">
                {registryNotice}
              </p>
            </div>
          ) : modelQuery.isLoading ? (
            <Skeleton className="h-48" />
          ) : tableData.length === 0 ? (
            <div className="flex h-full min-h-[320px] items-center justify-center">
              <p className="text-sm text-slate-400 text-center px-4">
                No rows returned for this model. Adjust filters or verify permissions on the backend.
              </p>
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-900/90 text-xs uppercase tracking-widest text-slate-500">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <th key={header.id} className="px-3 py-3">
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {virtualRows.map((virtualRow) => {
                  const row = table.getRowModel().rows[virtualRow.index];
                  return (
                    <tr
                      key={row.id}
                      className={`border-t border-white/5 text-xs text-slate-200 ${row.getIsSelected() ? "bg-slate-900/80" : ""}`}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-3 py-4">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-slate-400">
        <button
          onClick={() => setPage((prev) => Math.max(1, prev - 1))}
          disabled={!pagination || page <= 1}
          className="rounded-full border border-slate-800 px-4 py-2 text-xs uppercase tracking-widest"
        >
          Previous
        </button>
        <button
          onClick={() => {
            if (pagination && page < pagination.total_pages) {
              setPage((prev) => prev + 1);
            }
          }}
          disabled={!pagination || page >= pagination.total_pages}
          className="rounded-full border border-slate-800 px-4 py-2 text-xs uppercase tracking-widest"
        >
          Next
        </button>
        <span className="text-xs text-slate-500">
          Showing {tableData.length} rows (server-driven pagination)
        </span>
      </div>
    </div>
  );
}
