import pandas as pd
import numpy as np
from pathlib import Path

def main():
    report_path = Path('epubstandard_report.csv')
    if not report_path.exists():
        print(f"Error: '{report_path}' not found. Make sure you are in the correct directory.")
        return

    df = pd.read_csv(report_path)

    total_files = len(df)
    ok_files = df[df['status'] == 'OK'].shape[0]
    failed_files = total_files - ok_files

    ok_df = df[df['status'] == 'OK'].copy()
    ok_df['before_errors'] = pd.to_numeric(ok_df['before_errors'], errors='coerce')
    ok_df['after_errors'] = pd.to_numeric(ok_df['after_errors'], errors='coerce')
    ok_df.dropna(subset=['before_errors', 'after_errors'], inplace=True)

    if not ok_df.empty:
        ok_df['error_diff'] = ok_df['after_errors'] - ok_df['before_errors']
        errors_reduced = ok_df[ok_df['error_diff'] < 0].shape[0]
        errors_increased = ok_df[ok_df['error_diff'] > 0].shape[0]
        errors_same = ok_df[ok_df['error_diff'] == 0].shape[0]
        healed_used = ok_df[ok_df['used_healed'] == True].shape[0]
        total_errors_before = ok_df['before_errors'].sum()
        total_errors_after = ok_df['after_errors'].sum()
    else:
        errors_reduced, errors_increased, errors_same, healed_used = 0, 0, 0, 0
        total_errors_before, total_errors_after = 0, 0

    print("--- EPUB Processing Summary ---")
    print(f"\n✅ Total Files Processed: {total_files}")
    print(f"  - Succeeded: {ok_files}")
    print(f"  - Failed:    {failed_files}")

    if ok_files > 0:
        print("\n📊 Error Reduction Analysis (for succeeded files):")
        print(f"  - Files with fewer errors:  {errors_reduced}")
        print(f"  - Files with more errors:   {errors_increased}")
        print(f"  - Files with same errors:   {errors_same}")

        print("\n🩹 Healing Application:")
        print(f"  - 'Healed' version was applied: {healed_used} times")

        print("\n📈 Overall Error Statistics:")
        print(f"  - Total errors before: {int(total_errors_before)}")
        print(f"  - Total errors after:  {int(total_errors_after)}")
        print(f"  - Net change in errors: {int(total_errors_after - total_errors_before)}")
    
    # Print metrics from the new healer script
    if 'broken_links_removed' in df.columns:
        total_broken_links = df['broken_links_removed'].sum()
        if total_broken_links > 0:
            print("\n🔧 Healer Script Metrics:")
            print(f"  - Total broken internal links removed: {int(total_broken_links)}")

if __name__ == "__main__":
    main()