[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Account,
    [Parameter(Mandatory)]
    [ValidateSet('GrantRequired', 'RemoveRequired')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
if (-not ('RagLsaRights' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RagLsaRights {
  [StructLayout(LayoutKind.Sequential)]
  private struct LSA_OBJECT_ATTRIBUTES {
    public int Length; public IntPtr RootDirectory; public IntPtr ObjectName;
    public uint Attributes; public IntPtr SecurityDescriptor;
    public IntPtr SecurityQualityOfService;
  }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  private struct LSA_UNICODE_STRING {
    public ushort Length; public ushort MaximumLength;
    [MarshalAs(UnmanagedType.LPWStr)] public string Buffer;
  }
  [DllImport("advapi32.dll", SetLastError=true)]
  private static extern bool LookupAccountName(string system, string account,
    byte[] sid, ref uint sidSize, System.Text.StringBuilder domain,
    ref uint domainSize, out int use);
  [DllImport("advapi32.dll")]
  private static extern uint LsaOpenPolicy(IntPtr system,
    ref LSA_OBJECT_ATTRIBUTES attributes, uint access, out IntPtr policy);
  [DllImport("advapi32.dll")]
  private static extern uint LsaAddAccountRights(IntPtr policy, byte[] sid,
    LSA_UNICODE_STRING[] rights, uint count);
  [DllImport("advapi32.dll")]
  private static extern uint LsaRemoveAccountRights(IntPtr policy, byte[] sid,
    bool all, LSA_UNICODE_STRING[] rights, uint count);
  [DllImport("advapi32.dll")] private static extern uint LsaClose(IntPtr handle);
  [DllImport("advapi32.dll")] private static extern uint LsaNtStatusToWinError(uint status);
  private static byte[] Sid(string account) {
    uint sidSize=0, domainSize=0; int use;
    LookupAccountName(null, account, null, ref sidSize, null, ref domainSize, out use);
    byte[] sid=new byte[sidSize]; var domain=new System.Text.StringBuilder((int)domainSize);
    if(!LookupAccountName(null, account, sid, ref sidSize, domain, ref domainSize, out use))
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    return sid;
  }
  private static LSA_UNICODE_STRING[] Strings(string[] values) {
    var result=new LSA_UNICODE_STRING[values.Length];
    for(int i=0;i<values.Length;i++) result[i]=new LSA_UNICODE_STRING {
      Buffer=values[i], Length=(ushort)(values[i].Length*2),
      MaximumLength=(ushort)((values[i].Length+1)*2)};
    return result;
  }
  public static void Apply(string account, string[] add, string[] remove) {
    var attrs=new LSA_OBJECT_ATTRIBUTES(); attrs.Length=Marshal.SizeOf(attrs);
    IntPtr policy; uint status=LsaOpenPolicy(IntPtr.Zero, ref attrs, 0x810, out policy);
    if(status!=0) throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));
    try {
      byte[] sid=Sid(account);
      if(add.Length>0) {
        var rights=Strings(add); status=LsaAddAccountRights(policy,sid,rights,(uint)rights.Length);
        if(status!=0) throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));
      }
      if(remove.Length>0) {
        var rights=Strings(remove); status=LsaRemoveAccountRights(policy,sid,false,rights,(uint)rights.Length);
        if(status!=0 && status!=0xC0000034)
          throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));
      }
    } finally { LsaClose(policy); }
  }
}
'@
}

$required = @(
    'SeServiceLogonRight',
    'SeDenyInteractiveLogonRight',
    'SeDenyRemoteInteractiveLogonRight',
    'SeDenyBatchLogonRight',
    'SeDenyNetworkLogonRight'
)
$resolvedAccount = $Account
if ($resolvedAccount.StartsWith('.\', [StringComparison]::Ordinal)) {
    $resolvedAccount = '{0}\{1}' -f (
        [Environment]::MachineName
    ),$resolvedAccount.Substring(2)
}
if ($Action -ceq 'GrantRequired') {
    [RagLsaRights]::Apply($resolvedAccount, $required, @())
} else {
    [RagLsaRights]::Apply($resolvedAccount, @(), $required)
}
